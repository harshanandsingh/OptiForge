import os
import glob
import tempfile
import docker
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS

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



# -----------------------------------
#  Compilation API Endpoint
# -----------------------------------
# ... (keep imports and helper function the same) ...

# ... (all your imports and helper functions, including the new run_ai_coach) ...

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
        # comparison_results = {} # We'll do this in the next step

        try:
            # --- 1. RUN THE USER'S PRIMARY REQUEST ---
            # (We run this first so the user gets their main output)
            logging.info(f"Running primary job: {output_type}")
            if output_type == 'run':
                docker_cmd = f"bash -c '{compiler_cmd} {opt_flag} /io/{code_filename} -o /io/prog && timeout 2s /io/prog < /io/input.txt'"
                main_output = run_docker_container("my-compiler-image", docker_cmd, volumes)

            # elif output_type == 'coverage':
            #     if compiler == 'gcc': return jsonify({"error": "Coverage needs LLVM"}), 400
                
            #     # DEBUG VERSION: Adds echo statements to trace progress
            #     cmd = (
            #         f"bash -c '"
            #         f"echo \"[1] Compiling...\" && "
            #         f"{compiler_cmd} -fprofile-instr-generate -fcoverage-mapping /io/{code_filename} -o /io/prog && "
            #         f"echo \"[2] Running...\" && "
            #         f"LLVM_PROFILE_FILE=\"/io/code.profraw\" timeout 2s /io/prog < /io/input.txt > /dev/null && "
            #         f"echo \"[3] Merging Data...\" && "
            #         f"llvm-profdata merge -sparse /io/code.profraw -o /io/code.profdata && "
            #         f"echo \"[4] Generating Report...\" && "
            #         f"llvm-cov show /io/prog -instr-profile=/io/code.profdata /io/{code_filename} -use-color=false && "
            #         f"echo \"[5] Done!\""
            #         f"'"
            #     )
            #     main_output = run_docker_container("my-compiler-image", cmd, volumes)

            elif output_type == 'graph':
                if compiler == 'gcc':
                    main_output = "Error: CFG Graph only supported with LLVM"
                else:
                    cmd_ir = f"{compiler_cmd} -S -emit-llvm {opt_flag} /io/{code_filename} -o /io/code.ll"
                    run_docker_container("my-compiler-image", cmd_ir, volumes)
                    
                    cmd_dot = "opt -dot-cfg -disable-output code.ll"
                    run_docker_container("my-compiler-image", cmd_dot, volumes, workdir="/io")
                    
                    dot_files = glob.glob(os.path.join(temp_dir, '*.dot')) + \
                                glob.glob(os.path.join(temp_dir, '.*.dot'))
                    
                    if not dot_files:
                        main_output = "Error: No graph generated."
                    else:
                        final_dot_output = ""
                        for dot_file in dot_files:
                            with open(dot_file, 'r') as f: final_dot_output += f.read() + "\n"
                        main_output = final_dot_output
            
            else: # 'asm' or 'ir'
                flag = "-S" if output_type == 'asm' else "-S -emit-llvm"
                if compiler == 'gcc' and output_type == 'ir': flag = "-S"
                docker_cmd = f"{compiler_cmd} {flag} {opt_flag} /io/{code_filename} -o -"
                main_output = run_docker_container("my-compiler-image", docker_cmd, volumes)

            # --- 2. RUN THE AI OPTIMIZATION COACH ---
            # This runs after the main job, regardless of what it was
            ai_coach_results = run_ai_coach(compiler_cmd, code_filename, volumes, "/io")

            # --- 3. RUN COMPARISON (NEW!) ---
            # We pass the user's selected language and optimization flag
            comparison_results = run_comparison(lang, opt_flag, code_filename, volumes, "/io")

            # --- 4. SEND FINAL RESPONSE ---
            return jsonify({
                "output": main_output,
                "ai_coach": ai_coach_results,
                "comparison": comparison_results  
            })

        except RuntimeError as e:
            logging.error(str(e))
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            logging.exception("Unhandled error")
            return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)