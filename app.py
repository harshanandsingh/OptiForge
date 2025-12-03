import os
import glob
import tempfile
import docker
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
# --- NEW IMPORTS FOR GEMINI ---
from google import genai
from google.genai.errors import APIError
# ------------------------------

# --- NEW IMPORTS FOR ENV LOADING ---
from dotenv import load_dotenv
# --- END NEW IMPORTS ---

# --- LOAD ENVIRONMENT VARIABLES ---
# This looks for .env in the parent directory (intelligent-compiler-studio/)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
# --- END LOAD ENVIRONMENT VARIABLES ---

# -----------------------------------
#  Flask App & Docker Initialization
# -----------------------------------
app = Flask(__name__)
CORS(app)

# Initialize Docker client
client = docker.from_env()

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# -----------------------------------
#  Helper Function: Safe Docker Runner
# -----------------------------------
def run_docker_container(image, command, volumes, workdir="/io"):
    """
    A helper wrapper around Docker SDK to execute commands securely.
    """
    try:
        container = client.containers.run(
            image=image,
            command=command,
            volumes=volumes,
            remove=True,
            mem_limit="256m",
            cpu_period=100000,
            cpu_quota=50000,
            network_mode="none",
            working_dir=workdir,
            stdout=True,
            stderr=True,
            user="1000:1000",
            security_opt=["no-new-privileges:true"]
        )
        return container.decode("utf-8", errors="ignore")
    except docker.errors.ContainerError as e:
        return e.stderr.decode("utf-8", errors="ignore")
    except Exception as e:
        raise RuntimeError(f"Docker execution failed: {str(e)}")


# -----------------------------------
# best compiler based on the input code  
# -----------------------------------

def run_comparison(lang, opt_flag, code_filename, volumes, workdir):
    """
    Compiles the code with BOTH GCC and LLVM at the USER'S chosen
    optimization level to see which one is better for this specific code.
    """
    logging.info(f"[COMPARISON] Starting analysis for {opt_flag}...")
    
    # 1. Setup correct compiler names based on language
    if lang == 'c':
        gcc_cmd = "gcc"
        llvm_cmd = "clang"
    else:
        gcc_cmd = "g++"
        llvm_cmd = "clang++"

    metrics = {}

    # 2. Run analysis for both compilers
    for compiler in [gcc_cmd, llvm_cmd]:
        key = "gcc" if compiler == gcc_cmd else "llvm"
        try:
            # A. Speed Metric (Assembly line count)
            cmd_speed = f"bash -c '{compiler} -S {opt_flag} /io/{code_filename} -o - | wc -l'"
            speed_output = run_docker_container("my-compiler-image", cmd_speed, volumes, workdir)
            metrics[f"{key}_speed"] = int(speed_output.strip())

            # B. Size Metric (Object file size)
            # We use a unique filename for each so they don't overwrite each other incorrectly
            obj_file = f"/io/{key}.o"
            cmd_size = f"bash -c '{compiler} -c {opt_flag} /io/{code_filename} -o {obj_file} && stat -c %s {obj_file}'"
            size_output = run_docker_container("my-compiler-image", cmd_size, volumes, workdir)
            metrics[f"{key}_size"] = int(size_output.strip())

        except Exception as e:
            logging.warning(f"[COMPARISON] {compiler} failed: {e}")
            metrics[f"{key}_speed"] = 999999 # Massive penalty for failure
            metrics[f"{key}_size"] = 999999

    # 3. Generate Recommendation
    # Smaller is better for both metrics
    speed_winner = "LLVM" if metrics['llvm_speed'] < metrics['gcc_speed'] else "GCC"
    if metrics['llvm_speed'] == metrics['gcc_speed']: speed_winner = "Tie"

    size_winner = "LLVM" if metrics['llvm_size'] < metrics['gcc_size'] else "GCC"
    if metrics['llvm_size'] == metrics['gcc_size']: size_winner = "Tie"

    recommendation = (
        f"For Speed: {speed_winner} is better ({min(metrics['llvm_speed'], metrics['gcc_speed'])} lines).\n"
        f"For Size: {size_winner} is better ({min(metrics['llvm_size'], metrics['gcc_size'])} bytes)."
    )

    return {"recommendation": recommendation, "metrics": metrics}


# -----------------------------------
# best optimizaton based on our input 
# -----------------------------------

def run_ai_coach(compiler_cmd, code_filename, volumes, workdir):
    """
    Runs the code with all optimization levels to find the "best"
    for speed (fewest assembly lines) and size (smallest file size).
    """
    logging.info("[AI_COACH] Starting analysis...")
    optimizations = ['-O0', '-O1', '-O2', '-O3', '-Os']
    metrics = []

    for opt in optimizations:
        try:
            # 1. Get Speed Metric (Assembly line count)
            # -o - means output to stdout, | wc -l counts the lines
            cmd_speed = f"bash -c '{compiler_cmd} -S {opt} /io/{code_filename} -o - | wc -l'"
            speed_output = run_docker_container("my-compiler-image", cmd_speed, volumes, workdir)
            line_count = int(speed_output.strip())

            # 2. Get Size Metric (Object file size in bytes)
            # -c compiles to object file, && stat -c %s gets the file size
            cmd_size = f"bash -c '{compiler_cmd} -c {opt} /io/{code_filename} -o /io/code.o && stat -c %s /io/code.o'"
            size_output = run_docker_container("my-compiler-image", cmd_size, volumes, workdir)
            file_size = int(size_output.strip())

            metrics.append({"opt": opt, "speed_metric": line_count, "size_metric": file_size})
            logging.info(f"[AI_COACH] Metric for {opt}: {line_count} lines, {file_size} bytes")

        except Exception as e:
            # If one optimization fails to compile, just skip it and log
            logging.warning(f"[AI_COACH] Analysis failed for {opt}: {e}")
            metrics.append({"opt": opt, "speed_metric": float('inf'), "size_metric": float('inf')})
    
    # 3. Analyze the metrics
    if not metrics:
        return {"recommendation": "Could not analyze optimizations.", "metrics": []}

    best_speed = min(metrics, key=lambda x: x['speed_metric'])
    best_size = min(metrics, key=lambda x: x['size_metric'])

    recommendation = (
        f"For Speed: Use {best_speed['opt']} (Metric: {best_speed['speed_metric']} assembly lines).\n"
        f"For Size: Use {best_size['opt']} (Metric: {best_size['size_metric']} bytes)."
    )

    return {"recommendation": recommendation, "metrics": metrics}

# UPDATED: Helper function to generate CFG artifact
def run_cfg_generation(compiler_cmd, opt_flag, code_filename, volumes, workdir, temp_dir):
    """
    Generates CFG as a separate artifact if the compiler is LLVM.
    MODIFIED: Explicitly look for the 'main' function dot file to avoid rendering
    compiler-generated initialization function graphs.
    """
    logging.info("[CFG] Starting generation...")
    cfg_output = ""
    
    # Check if compiler is LLVM (clang)
    if compiler_cmd.startswith('clang'):
        try:
            # 1. Generate LLVM IR
            cmd_ir = f"{compiler_cmd} -S -emit-llvm {opt_flag} /io/{code_filename} -o /io/code.ll"
            run_docker_container("my-compiler-image", cmd_ir, volumes)
            
            # 2. Generate DOT graph from IR
            # This generates multiple .dot files (e.g., .main.dot, .__GLOBAL__sub_I_main.cpp.dot)
            cmd_dot = "opt -dot-cfg -disable-output code.ll"
            run_docker_container("my-compiler-image", cmd_dot, volumes, workdir="/io")
            
            # 3. Read the DOT file for the main function
            # The naming convention is typically '.main.dot'
            main_dot_file = os.path.join(temp_dir, '.main.dot') 
            
            if os.path.exists(main_dot_file):
                with open(main_dot_file, 'r') as f: 
                    cfg_output = f.read()
            else:
                # Fallback/Error handling
                dot_files = glob.glob(os.path.join(temp_dir, '*.dot')) + \
                            glob.glob(os.path.join(temp_dir, '.*.dot'))
                
                if not dot_files:
                    cfg_output = "Error: No graph generated by 'opt -dot-cfg'. Check code, optimization level, and LLVM version compatibility."
                else:
                    # As a fallback, concatenate all, but log a warning.
                    logging.warning("[CFG] .main.dot not found. Concatenating all dot files. Graph might be incorrect.")
                    final_dot_output = ""
                    for dot_file in dot_files: 
                        with open(dot_file, 'r') as f: 
                            final_dot_output += f.read() + "\n"
                    cfg_output = final_dot_output

        except Exception as e:
            cfg_output = f"Error during CFG generation (LLVM): {str(e)}"
    else:
        cfg_output = "Error: CFG Graph only supported with LLVM compiler."
        
    return cfg_output


# -----------------------------------
#  Compilation API Endpoint
# -----------------------------------

# -----------------------------------
#  LLVM Pass Analysis Endpoint
# -----------------------------------

@app.route("/api/llvm-pass", methods=['POST'])
def apply_llvm_pass():
    """
    Applies an LLVM pass to the user's code via opt.
    Supported passes include: opcode-counter, and other custom passes.
    """
    data = request.json
    code = data.get('code', '')
    lang = data.get('language', 'c')
    pass_name = data.get('pass', 'opcode-counter')  # e.g., 'opcode-counter'
    opt_level = data.get('optimization', '-O0')
    
    # Validate pass name (prevent injection attacks)
    allowed_passes = ['opcode-counter']  # Expand this as you add more passes
    if pass_name not in allowed_passes:
        return jsonify({"error": f"Unknown pass: {pass_name}. Allowed passes: {allowed_passes}"}), 400
    
    ext = ".c" if lang == 'c' else ".cpp"
    compiler_cmd = "clang" if lang == 'c' else "clang++"
    
    with tempfile.TemporaryDirectory() as temp_dir:
        code_filename = f"main{ext}"
        code_path = os.path.join(temp_dir, code_filename)
        ir_path = os.path.join(temp_dir, "code.ll")
        
        # 1. Write the source code to file
        with open(code_path, 'w') as f:
            f.write(code)
        
        volumes = {temp_dir: {'bind': '/io', 'mode': 'rw'}}
        
        try:
            # 2. Generate LLVM IR (disable optnone attribute so passes can run)
            logging.info(f"[LLVM_PASS] Generating LLVM IR with optimization {opt_level}...")
            cmd_ir = (
                f"{compiler_cmd} -Xclang -disable-O0-optnone -fno-discard-value-names "
                f"{opt_level} -S -emit-llvm /io/{code_filename} -o /io/code.ll"
            )
            run_docker_container("my-compiler-image", cmd_ir, volumes, "/io")
            
            # 3. Apply the LLVM pass
            logging.info(f"[LLVM_PASS] Applying pass: {pass_name}...")
            if pass_name == 'opcode-counter':
                cmd_pass = (
                    f"opt -load-pass-plugin=/opt/llvm-passes/libOpcodeCounter.so "
                    f"-passes='function(opcode-counter)' -disable-output /io/code.ll"
                )
            else:
                return jsonify({"error": f"Pass handler not implemented: {pass_name}"}), 500
            
            pass_output = run_docker_container("my-compiler-image", cmd_pass, volumes, "/io")
            
            # 4. Also return the IR for debugging/inspection
            with open(ir_path, 'r') as f:
                ir_content = f.read()
            
            return jsonify({
                "pass_name": pass_name,
                "pass_output": pass_output,
                "ir": ir_content
            })
        
        except Exception as e:
            logging.exception(f"[LLVM_PASS] Error applying pass: {e}")
            return jsonify({"error": str(e)}), 500


@app.route("/api/compile", methods=['POST'])
def compile_code():
    data = request.json
    code = data.get('code', '')
    user_input = data.get('input', '')
    
    lang = data.get('language', 'cpp')
    compiler = data.get('compiler', 'llvm')
    opt_flag = data.get('optimization', '-O0')
    output_type = data.get('output_type', 'asm')

    ext = ".c" if lang == 'c' else ".cpp"
    compiler_cmd = "clang" if lang == 'c' else "clang++"
    if compiler == 'gcc':
        compiler_cmd = "gcc" if lang == 'c' else "g++"

    with tempfile.TemporaryDirectory() as temp_dir:
        code_filename = f"main{ext}"
        with open(os.path.join(temp_dir, code_filename), 'w') as f: f.write(code)
        with open(os.path.join(temp_dir, 'input.txt'), 'w') as f: f.write(user_input)

        volumes = { temp_dir: {'bind': '/io', 'mode': 'rw'} }

        # --- Create containers for our final JSON response ---
        main_output = ""
        ai_coach_results = {}
        comparison_results = {}
        cfg_output = "" # NEW: Variable for CFG output
        top_level_error = None # ADDED: New variable to hold the error text

        try:
            # --- 1. RUN THE USER'S PRIMARY REQUEST ---
            logging.info(f"Running primary job: {output_type}")
            if output_type == 'run':
                docker_cmd = f"bash -c '{compiler_cmd} {opt_flag} /io/{code_filename} -o /io/prog && timeout 2s /io/prog < /io/input.txt'"
                main_output = run_docker_container("my-compiler-image", docker_cmd, volumes)
            
            # MODIFIED: Wrap command in 'bash -c' for reliable stdout capture
            else: # 'asm' or 'ir' (and 'Errors' which maps to 'asm')
                flag = "-S" if output_type == 'asm' else "-S -emit-llvm"
                if compiler == 'gcc' and output_type == 'ir': flag = "-S"
                
                raw_cmd = f"{compiler_cmd} {flag} {opt_flag} /io/{code_filename} -o -"
                docker_cmd = f"bash -c '{raw_cmd}'" 
                
                main_output = run_docker_container("my-compiler-image", docker_cmd, volumes)

            # --- NEW FIX: Detect compilation failure from the main_output (stderr) ---
            # If the compiler output contains an error, we treat it as a failure 
            # and short-circuit the rest of the analysis jobs, returning the error immediately.
            # Check for common compiler error/fatal error messages
            if "error:" in main_output.lower() or "fatal error:" in main_output.lower():
                
                top_level_error = main_output
                logging.error(f"Compilation failed, short-circuiting: {main_output.splitlines()[0]}")
                
                # Immediately return the error response (still 200 OK, but with error payload)
                return jsonify({
                    "output": "",  # Clear the output field
                    "ai_coach": {"recommendation": "Compilation failed, analysis skipped.", "metrics": []},
                    "comparison": {"recommendation": "Compilation failed, comparison skipped.", "metrics": {}},
                    "cfg_output": "Compilation failed, CFG generation skipped.",
                    "error": top_level_error # Send the compiler error message in the 'error' field
                })
            # --------------------------------------------------------------------------

            # --- 2. RUN THE AI OPTIMIZATION COACH (Unconditional) ---
            ai_coach_results = run_ai_coach(compiler_cmd, code_filename, volumes, "/io")

            # --- 3. RUN COMPARISON METRICS AND DISPLAY ASSEMBLY (Unconditional) ---
            comparison_results = run_comparison(lang, opt_flag, code_filename, volumes, "/io")

            # DEFERRED: Fix 6 logic is skipped here as per user request to defer
            comparison_results['llvm_asm'] = "Fix Deferred: The actual LLVM assembly code will be here after Fix 6 is applied." 
            comparison_results['gcc_asm'] = "Fix Deferred: The actual GCC assembly code will be here after Fix 6 is applied."

            # --- 4. RUN CFG GENERATION (Unconditional) ---
            cfg_output = run_cfg_generation(compiler_cmd, opt_flag, code_filename, volumes, "/io", temp_dir)
            
            # --- 5. SEND FINAL RESPONSE ---
            return jsonify({
                "output": main_output,
                "ai_coach": ai_coach_results,
                "comparison": comparison_results,
                "cfg_output": cfg_output, # NEW: Include CFG output
                "error": top_level_error # Should be None on success
            })

        except RuntimeError as e:
            logging.error(str(e))
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            logging.exception("Unhandled error")
            return jsonify({"error": str(e)}), 500


# -----------------------------------
#  Gemini API Helper Function (NEW)
# -----------------------------------
def explain_code_with_gemini(code, lang):
    """
    Calls the Google Gemini API to explain the provided code.
    Assumes GEMINI_API_KEY is set in the environment.
    """
    logging.info(f"[GEMINI_EXPLAIN] Calling Gemini API for {lang} code...")
    
    # Client will automatically pick up the GEMINI_API_KEY from the environment
    try:
        # Client will now find the key loaded by load_dotenv
        client = genai.Client() 
    except Exception:
        # Check if the key is missing or invalid
        if not os.getenv("GEMINI_API_KEY"):
            # UPDATED: More informative error message
            raise RuntimeError("Gemini Client failed: GEMINI_API_KEY is missing from environment. Ensure it is set in your .env file or terminal.")
        raise RuntimeError("Gemini Client initialization failed.")

    prompt = (
        f"You are an expert compiler and static analysis assistant. Explain the following {lang} code in detail. "
        "The explanation should cover a high-level overview, a line-by-line breakdown of key logic, "
        "a brief complexity analysis (time/space), and suggestions for improvement. Format the output using markdown.\n\n"
        f"Code:\n```\n{code}\n```"
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text
    except APIError as e:
        logging.error(f"[GEMINI_EXPLAIN] Gemini API Error: {e}")
        raise RuntimeError(f"Gemini API call failed. Details: {e}")
    except Exception as e:
        logging.error(f"[GEMINI_EXPLAIN] Unhandled Error: {e}")
        raise RuntimeError("An unexpected error occurred during Gemini processing.")


# -----------------------------------
#  New API Endpoint: AI Explain (/api/v1/gemini/explain) (NEW)
# -----------------------------------
@app.route("/api/v1/gemini/explain", methods=['POST'])
def gemini_explain():
    data = request.json
    code = data.get('code', '')
    
    # The frontend application targets C/C++, so we assume the language context is C++
    lang = "C/C++" 

    if not code or code.strip() == "":
        return jsonify({"error": "No code provided for explanation"}), 400

    try:
        explanation = explain_code_with_gemini(code, lang)
        return jsonify({
            "explanation": explanation,
            "status": "success"
        })
    except RuntimeError as e:
        # Catch and return the explicit errors raised by the helper function
        return jsonify({"error": str(e)}), 500
    except Exception:
        return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":

    app.run(debug=True, port=5000)
