import os
import glob
import tempfile
import docker
import logging
import subprocess
import uuid
from flask import Flask, request, jsonify
from flask_cors import CORS
# --- NEW IMPORTS FOR GEMINI ---
from google import genai
# Fix unresolved import
try:
    from google.genai.errors import APIError
except ImportError:
    logging.warning("google.genai.errors module not found. Ensure it's installed if needed.")
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

# --- CONFIGURATION FOR LLVM PIPELINE ---
# Update this path to point to your compiled .so file
OPCODE_PASS_PATH = "/opt/llvm-passes/libOpcodeCounter.so"

# Map user-friendly names to LLVM internal pass names
# This acts as a whitelist to prevent command injection
PASS_MAPPING = {
    "mem2reg": "mem2reg",
    "instcombine": "instcombine",
    "dce": "dce",
    "adce": "adce",
    "loop-unroll": "loop-unroll",
    "gvn": "gvn",
    "simplifycfg": "simplifycfg",
    "opcode-counter": "opcode-counter"
}

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
# Helper function for running shell commands (from app1.py)
# -----------------------------------
def run_command(cmd):
    """Helper to run shell commands and catch errors."""
    try:
        # shell=True is used here for simplicity in calling complex LLVM commands
        # capture_output=True captures both stdout and stderr
        result = subprocess.run(
            cmd, shell=True, check=True, capture_output=True, text=True
        )
        return result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        # Return the standard error if the command failed (e.g., compilation error)
        return None, e.stderr


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
            # 2. Generate DOT graph from IR
            # This generates multiple .dot files (e.g., .main.dot, .__GLOBAL__sub_I_main.cpp.dot)
            # Use explicit path /io/code.ll and capture output
            # FIX: Use -passes=dot-cfg for New PM
            cmd_dot = "opt -passes=dot-cfg -disable-output /io/code.ll"
            dot_output = run_docker_container("my-compiler-image", cmd_dot, volumes, workdir="/io")
            
            # 3. Read the DOT file for the main function
            # The naming convention is typically '.main.dot'
            main_dot_file = os.path.join(temp_dir, '.main.dot') 
            
            if os.path.exists(main_dot_file):
                # --- NEW: Try to convert to SVG on backend ---
                # This bypasses frontend rendering issues
                try:
                    cmd_svg = "dot -Tsvg -o /io/graph.svg /io/.main.dot"
                    svg_out_log = run_docker_container("my-compiler-image", cmd_svg, volumes, workdir="/io")
                    
                    svg_file = os.path.join(temp_dir, 'graph.svg')
                    if os.path.exists(svg_file):
                         with open(svg_file, 'r') as f:
                             cfg_output = f.read()
                             # Log success
                             logging.info("[CFG] Successfully generated SVG.")
                    else:
                        # Fallback to DOT if dot command failed/missing
                        with open(main_dot_file, 'r') as f: 
                             cfg_output = f.read()
                except Exception:
                    # Fallback to DOT
                    with open(main_dot_file, 'r') as f: 
                        cfg_output = f.read()
            else:
                # Fallback/Error handling
                dot_files = glob.glob(os.path.join(temp_dir, '*.dot')) + \
                            glob.glob(os.path.join(temp_dir, '.*.dot'))
                
                if not dot_files:
                    # Provide details in case of failure
                    cfg_output = (
                        f"Error: No graph generated by 'opt -passes=dot-cfg'.\n"
                        f"Opt Output: {dot_output}\n"
                        f"Files found: {os.listdir(temp_dir)}"
                    )
                else:
                    # As a fallback, concatenate all, but log a warning.
                    logging.warning("[CFG] .main.dot not found. Concatenating all dot files.")
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
def llvm_pass():
    """
    Applies an LLVM pass to the user's code via opt.
    Supported passes include: opcode-counter, and other custom passes.
    """
    try:
        data = request.json
        logging.info(f"[LLVM_PASS] Received request data: {data}")
        
        # Extract parameters from request
        code = data.get('code', '')
        pass_name = data.get('pass_name')
        language = data.get('language', 'cpp')
        opt_level = data.get('optimization', '-O0')
        
        logging.info(f"[LLVM_PASS] pass_name: '{pass_name}', Available passes: {list(PASS_MAPPING.keys())}")

        if not pass_name:
            return jsonify({"error": "Missing pass_name parameter"}), 400
        
        # VALIDATION FIX: Handle both list and string types
        if isinstance(pass_name, list):
            invalid_passes = [p for p in pass_name if p not in PASS_MAPPING]
            if invalid_passes:
                 return jsonify({"error": f"Invalid pass_names {invalid_passes}. Available passes: {list(PASS_MAPPING.keys())}"}), 400
        elif pass_name not in PASS_MAPPING:
             return jsonify({"error": f"Invalid pass_name '{pass_name}'. Available passes: {list(PASS_MAPPING.keys())}"}), 400

        if not code:
            return jsonify({"error": "Missing code parameter"}), 400

        # Create temporary directory and files
        with tempfile.TemporaryDirectory() as temp_dir:
            # Determine file extension
            ext = ".cpp" if language == "cpp" else ".c"
            src_file = os.path.join(temp_dir, f"code{ext}")
            ir_file = os.path.join(temp_dir, "code.ll")
            
            # Write source code to file
            with open(src_file, 'w') as f:
                f.write(code)
            
            # Set up Docker volumes
            volumes = {temp_dir: {'bind': '/io', 'mode': 'rw'}}
            
            # Generate LLVM IR
            logging.info(f"[LLVM_PASS] Generating LLVM IR with optimization {opt_level}...")
            compiler_cmd = "clang++" if language == "cpp" else "clang"
            cmd_ir = (
                f"{compiler_cmd} -Xclang -disable-O0-optnone -fno-discard-value-names "
                f"{opt_level} -S -emit-llvm /io/code{ext} -o /io/code.ll"
            )
            ir_output = run_docker_container("my-compiler-image", cmd_ir, volumes, "/io")
            
            # Check if IR file was created
            if not os.path.exists(ir_file):
                return jsonify({"error": f"Failed to generate LLVM IR: {ir_output}"}), 500

            # Apply the LLVM pass
            logging.info(f"[LLVM_PASS] Applying passes: {pass_name}...")
            
            pass_output = ""
            optimized_ir_content = ""

            # Check if pass_name is a list (new frontend) or string (old frontend fallback)
            # We normalize to a list named 'pass_list'
            if isinstance(pass_name, list):
                pass_list = pass_name
            else:
                pass_list = [pass_name]

            # --- NEW: COMPARATIVE ANALYSIS LOGIC ---
            
            # Helper to get instruction count using opcode-counter
            def get_instruction_count(target_file):
                cmd_count = (
                    f"bash -c 'opt -load-pass-plugin={OPCODE_PASS_PATH} "
                    f"-passes=\"{PASS_MAPPING['opcode-counter']}\" -disable-output {target_file}'"
                )
                output = run_docker_container("my-compiler-image", cmd_count, volumes, "/io")
                return output

            # 1. BASELINE METRICS
            logging.info("[LLVM_PASS] Calculating baseline metrics...")
            base_stats_raw = get_instruction_count("/io/code.ll")
            
            # Simple parser to extract total count
            def parse_total_inst(raw_output):
                total = 0
                try:
                    for line in raw_output.splitlines():
                        parts = line.split(':')
                        if len(parts) == 2:
                            try:
                                count = int(parts[1].strip())
                                total += count
                            except ValueError:
                                pass
                except Exception:
                    pass
                return total

            base_count = parse_total_inst(base_stats_raw)

            # 2. SEPARATE PASSES
            transform_passes = [p for p in pass_list if p != "opcode-counter" and p in PASS_MAPPING]
            
            pass_output = ""
            optimized_ir_content = ""
            final_ir_file = ir_file # Default to original if no transforms

            # 3. RUN TRANSFORMATIONS (STEP-BY-STEP)
            step_reports = []
            
            # Start with original IR
            current_ir_file = "code.ll" 
            current_count = base_count
            
            if transform_passes:
                logging.info(f"[LLVM_PASS] Running step-by-step pipeline: {transform_passes}")
                
                for idx, pass_cmd in enumerate(transform_passes):
                    pass_internal_name = PASS_MAPPING[pass_cmd]
                    
                    # Define names for this step
                    output_ir_filename = f"code_step_{idx}.ll"
                    
                    # Run ONE pass
                    cmd_pass = (
                        f"bash -c 'opt -passes=\"{pass_internal_name}\" -S /io/{current_ir_file} -o /io/{output_ir_filename}'"
                    )
                    container_output = run_docker_container("my-compiler-image", cmd_pass, volumes, "/io")
                    
                    if "error:" in container_output.lower():
                        return jsonify({"error": f"Pass '{pass_cmd}' failed: {container_output}"}), 500
                    
                    if not os.path.exists(os.path.join(temp_dir, output_ir_filename)):
                         return jsonify({"error": f"Pass '{pass_cmd}' did not generate output."}), 500
                    
                    # Analyze Result of this step
                    step_stats_raw = get_instruction_count(f"/io/{output_ir_filename}")
                    step_count = parse_total_inst(step_stats_raw)
                    delta = current_count - step_count
                    
                    step_reports.append({
                        "pass": pass_cmd,
                        "count": step_count,
                        "delta": delta
                    })
                    
                    # Update state for next iteration
                    current_ir_file = output_ir_filename
                    current_count = step_count

                # Finalize
                final_ir_path = os.path.join(temp_dir, current_ir_file)
                with open(final_ir_path, 'r') as f:
                    optimized_ir_content = f.read()
                    
                pass_output = optimized_ir_content
                ir_content = optimized_ir_content
                final_ir_file = final_ir_path
                
            else:
                # No transformations, just return original IR
                final_ir_file = ir_file
                current_count = base_count
                with open(ir_file, 'r') as f:
                    pass_output = f.read()
                    ir_content = pass_output

            # 4. OPTIMIZED METRICS (Final check)
            # (Already tracked in current_count, but let's be consistent with final file)
            opt_count = current_count 
            opt_stats_raw = get_instruction_count(f"/io/{os.path.basename(final_ir_file)}")

            # 5. GENERATE DETAILED ANALYSIS REPORT
            total_reduction = base_count - opt_count
            reduction_pct = (total_reduction / base_count * 100) if base_count > 0 else 0
            
            analysis_header = "\n\n" + ("-" * 60) + "\n"
            analysis_header += f"Step-by-Step Optimization Report\n"
            analysis_header += ("-" * 60) + "\n"
            
            summary = f"Baseline:      {base_count} instructions\n\n"
            
            for i, report in enumerate(step_reports):
                sign = "-" if report['delta'] >= 0 else "+"
                delta_str = f"({sign}{abs(report['delta'])})"
                # Formatting: "1. mem2reg:       45 instructions (-5)"
                summary += f"{i+1}. {report['pass']:<14}: {report['count']} instructions {delta_str}\n"
            
            summary += f"\nFinal Count:   {opt_count} instructions\n"
            summary += f"Total Reduction: {total_reduction} instructions ({reduction_pct:.2f}%)\n"

            # If Opcode Counter was explicitly requested, append the full breakdown
            detailed_report = ""
            if "opcode-counter" in pass_list:
                detailed_report = f"\nDetailed Opcode Counts (Optimized):\n{opt_stats_raw}"
            
            # Combine everything
            pass_output += analysis_header + summary + detailed_report

            # --- NEW: GENERATE CFG FOR TRANSFORMED IR ---
            # We reuse the existing helper run_cfg_generation
            # But run_cfg_generation makes its own IR usually. We want CFG of *our* final IR.
            # So we'll adapt slightly: manually run dot-cfg on final_ir_file.
            
            cfg_output = ""
            try:
                # 1. Generate DOT graph from Final IR
                # The filename in Docker is os.path.basename(final_ir_file) inside /io
                final_ir_basename = os.path.basename(final_ir_file)
                # FIX: Use -passes=dot-cfg for New PM
                cmd_dot = f"opt -passes=dot-cfg -disable-output /io/{final_ir_basename}"
                run_docker_container("my-compiler-image", cmd_dot, volumes, workdir="/io")
                
                # 2. Read the DOT file for the main function
                # Naming convention: .main.dot (but hidden). 
                # Since we ran it in /io (which is temp_dir), we can look there.
                main_dot_file = os.path.join(temp_dir, '.main.dot') 
                
                if os.path.exists(main_dot_file):
                    with open(main_dot_file, 'r') as f: 
                        cfg_output = f.read()
                else:
                    # Fallback: find any .dot file
                    dot_files = glob.glob(os.path.join(temp_dir, '*.dot')) + \
                                glob.glob(os.path.join(temp_dir, '.*.dot'))
                    # Filter out the input source.dot if any
                    
                    if dot_files:
                         # Just concatenate them
                        final_dot_output = ""
                        for dot_file in dot_files: 
                            with open(dot_file, 'r') as f: 
                                final_dot_output += f.read() + "\n"
                        cfg_output = final_dot_output
                    else:
                        cfg_output = "Error: No CFG generated."
            except Exception as e:
                cfg_output = f"Error generating CFG: {str(e)}"
            # --------------------------------------------

            return jsonify({
                "pass_name": pass_list, 
                "pass_output": pass_output,
                "ir": ir_content,
                "cfg_output": cfg_output, # Return the CFG
                "status": "success"
            })

    except Exception as e:
        logging.exception(f"[LLVM_PASS] Error applying pass: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/compile", methods=['POST'])
def compile_code():
    """Unified compilation endpoint combining Docker and direct LLVM approaches"""
    data = request.json
    
    # Extract parameters for both approaches
    code = data.get('code', '')
    user_input = data.get('input', '')
    lang = data.get('language', 'cpp')
    compiler = data.get('compiler', 'llvm')
    opt_flag = data.get('optimization', '-O0')
    output_type = data.get('output_type', 'asm')
    
    # Direct LLVM parameters
    user_passes = data.get("passes", [])  # e.g. ["mem2reg", "dce"]
    outputs_requested = data.get("outputs", [])  # e.g. ["ir", "arm", "opcode_count"]
    use_direct_llvm = data.get("use_direct_llvm", False)  # Flag to choose compilation mode
    
    # Normalize language parameter
    if lang == 'cpp':
        lang = 'c++'
    
    ext = ".c" if lang == 'c' else ".cpp"
    compiler_cmd = "clang" if lang == 'c' else "clang++"
    if compiler == 'gcc':
        compiler_cmd = "gcc" if lang == 'c' else "g++"

    # --- DIRECT LLVM MODE ---
    if use_direct_llvm or outputs_requested:
        # Generate a unique ID for this request to avoid file collisions
        req_id = str(uuid.uuid4())[:8]
        base_name = f"temp_{req_id}"
        
        # Determine file extension and compiler driver for LLVM 19
        if lang == "c++":
            src_file = f"{base_name}.cpp"
            compiler_driver = "clang++-19"
        else:
            src_file = f"{base_name}.c"
            compiler_driver = "clang-19"
        
        # Save Source Code to Disk
        with open(src_file, "w") as f:
            f.write(code)
        
        response = {"status": "success", "errors": "", "mode": "direct_llvm"}
        
        try:
            # --- STAGE 1: COMPILE TO BASE IR ---
            base_ir_file = f"{base_name}.ll"
            cmd_compile = f"{compiler_driver} -S -emit-llvm {src_file} -o {base_ir_file} -O0 -Xclang -disable-O0-optnone"
            
            _, err = run_command(cmd_compile)
            if err and "error" in err.lower():
                raise Exception(f"Compilation Error:\n{err}")
            
            # --- STAGE 2: APPLY PASSES (OPTIMIZATION) ---
            optimized_ir_file = f"{base_name}_opt.ll"
            
            if compiler == "llvm":
                if not user_passes:
                    run_command(f"cp {base_ir_file} {optimized_ir_file}")
                else:
                    valid_passes = [PASS_MAPPING[p] for p in user_passes if p in PASS_MAPPING]
                    if valid_passes:
                        pass_args = ",".join(valid_passes)
                        cmd_opt = f"opt-19 -S -passes='{pass_args}' {base_ir_file} -o {optimized_ir_file}"
                        _, err = run_command(cmd_opt)
                        if err and "error" in err.lower():
                            raise Exception(f"Optimization Error:\n{err}")
                    else:
                        run_command(f"cp {base_ir_file} {optimized_ir_file}")
            else:
                response["warnings"] = "Custom LLVM passes are not supported when 'GCC' is selected. Returning raw IR."
                run_command(f"cp {base_ir_file} {optimized_ir_file}")
            
            # --- STAGE 3: GENERATE OUTPUTS ---
            if "ir" in outputs_requested:
                if os.path.exists(optimized_ir_file):
                    with open(optimized_ir_file, "r") as f:
                        response["ir"] = f.read()
            
            if "arm" in outputs_requested:
                asm_file = f"{base_name}.s"
                cmd_asm = f"llc-19 -march=aarch64 -filetype=asm {optimized_ir_file} -o {asm_file}"
                _, err = run_command(cmd_asm)
                if os.path.exists(asm_file):
                    with open(asm_file, "r") as f:
                        response["arm"] = f.read()
                else:
                    response["arm"] = f"Error generating ASM: {err}"
            
            if "opcode_count" in outputs_requested:
                cmd_count = f"opt-19 -load-pass-plugin={OPCODE_PASS_PATH} -passes=\"opcode-counter\" -disable-output {optimized_ir_file}"
                _, stderr = run_command(cmd_count)
                response["opcode_count"] = stderr
            
            # Add traditional output if requested
            if output_type == 'ir' and "ir" not in outputs_requested:
                response["output"] = response.get("ir", "")
            elif output_type == 'asm' and "arm" not in outputs_requested:
                response["output"] = response.get("arm", "")
            
        except Exception as e:
            response["status"] = "error"
            response["errors"] = str(e)
        
        finally:
            # Cleanup temporary files
            for ext_clean in [".c", ".cpp", ".ll", "_opt.ll", ".s"]:
                f = f"{base_name}{ext_clean}"
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except:
                        pass
        
        return jsonify(response)
    
    # --- DOCKER MODE (Original comprehensive analysis) ---
    with tempfile.TemporaryDirectory() as temp_dir:
        code_filename = f"main{ext}"
        with open(os.path.join(temp_dir, code_filename), 'w') as f: 
            f.write(code)
        with open(os.path.join(temp_dir, 'input.txt'), 'w') as f: 
            f.write(user_input)

        volumes = {temp_dir: {'bind': '/io', 'mode': 'rw'}}

        # Response containers
        main_output = ""
        ai_coach_results = {}
        comparison_results = {}
        cfg_output = ""
        top_level_error = None
        
        response = {"mode": "docker"}

        try:
            # --- 1. RUN THE USER'S PRIMARY REQUEST ---
            logging.info(f"Running primary job: {output_type}")
            if output_type == 'run':
                docker_cmd = f"bash -c '{compiler_cmd} {opt_flag} /io/{code_filename} -o /io/prog && timeout 2s /io/prog < /io/input.txt'"
                main_output = run_docker_container("my-compiler-image", docker_cmd, volumes)
            else:
                flag = "-S" if output_type == 'asm' else "-S -emit-llvm"
                if compiler == 'gcc' and output_type == 'ir': 
                    flag = "-S"
                
                raw_cmd = f"{compiler_cmd} {flag} {opt_flag} /io/{code_filename} -o -"
                docker_cmd = f"bash -c '{raw_cmd}'"
                main_output = run_docker_container("my-compiler-image", docker_cmd, volumes)

            # Check for compilation errors
            if "error:" in main_output.lower() or "fatal error:" in main_output.lower():
                top_level_error = main_output
                logging.error(f"Compilation failed: {main_output.splitlines()[0]}")
                
                return jsonify({
                    "output": "",
                    "ai_coach": {"recommendation": "Compilation failed, analysis skipped.", "metrics": []},
                    "comparison": {"recommendation": "Compilation failed, comparison skipped.", "metrics": {}},
                    "cfg_output": "Compilation failed, CFG generation skipped.",
                    "error": top_level_error,
                    "mode": "docker"
                })

            # --- 2. RUN COMPREHENSIVE ANALYSIS ---
            ai_coach_results = run_ai_coach(compiler_cmd, code_filename, volumes, "/io")
            comparison_results = run_comparison(lang, opt_flag, code_filename, volumes, "/io")
            
            # Add assembly outputs to comparison
            comparison_results['llvm_asm'] = "Fix Deferred: The actual LLVM assembly code will be here after Fix 6 is applied."
            comparison_results['gcc_asm'] = "Fix Deferred: The actual GCC assembly code will be here after Fix 6 is applied."
            
            cfg_output = run_cfg_generation(compiler_cmd, opt_flag, code_filename, volumes, "/io", temp_dir)
            
            return jsonify({
                "output": main_output,
                "ai_coach": ai_coach_results,
                "comparison": comparison_results,
                "cfg_output": cfg_output,
                "error": top_level_error,
                "mode": "docker"
            })

        except RuntimeError as e:
            logging.error(str(e))
            return jsonify({"error": str(e), "mode": "docker"}), 500
        except Exception as e:
            logging.exception("Unhandled error")
            return jsonify({"error": str(e), "mode": "docker"}), 500


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

    app.run(debug=True, port=5000, use_reloader=True)