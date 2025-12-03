# OptiForge Backend - LLVM Pass Integration

##  Overview

This backend now supports **LLVM passes** - advanced code analysis tools that can examine and analyze compiled code at the LLVM intermediate representation (IR) level. Users can request specific LLVM passes via the frontend to analyze instruction frequencies, optimize code, and gain insights into compiler behavior.

## What's New

### New Endpoint: `/api/llvm-pass`

```http
POST /api/llvm-pass
Content-Type: application/json
```

**Example Request:**
```json
{
  "code": "int main() { int a = 5; int b = 10; return a + b; }",
  "language": "c",
  "pass": "opcode-counter",
  "optimization": "-O0"
}
```

**Example Response:**
```json
{
  "pass_name": "opcode-counter",
  "pass_output": "---------------------------------------------\nOpcode Counts for Function: main\nadd : 1\nalloca : 3\nload : 2\nret : 1\nstore : 3\n---------------------------------------------\n",
  "ir": "[LLVM IR code]"
}
```

---

## 🚀 Quick Start

### 1. Build the LLVM Pass Plugin

```bash
cd /home/cuppycake/Projects/OptiForge/LLVMProject/OpcodeCounter
mkdir -p build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make -j$(nproc)
```

**Expected output:** `libOpcodeCounter.so` file should be created.

### 2. Build Docker Image

```bash
cd /home/cuppycake/Projects/OptiForge-Backend
docker build -t my-compiler-image .
```

**Note:** This may take 3-5 minutes on first build due to LLVM installation.

### 3. Start Backend

```bash
cd /home/cuppycake/Projects/OptiForge-Backend
# Set up Python environment (if not done)
python3 -m venv venv
source venv/bin/activate
pip install flask flask-cors docker requests

# Start the backend
python3 app.py
```

### 4. Test Integration

```bash
# Run comprehensive test
python3 integration_test.py

# Expected output: "All tests passed! Integration is working correctly."
```

---

## API Reference

### Request Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `code` | string | Yes | - | C/C++ source code |
| `language` | string | No | "c" | "c" or "cpp" |
| `pass` | string | No | "opcode-counter" | LLVM pass to apply |
| `optimization` | string | No | "-O0" | "-O0", "-O1", "-O2", "-O3", "-Os" |

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `pass_name` | string | Name of the applied pass |
| `pass_output` | string | Analysis results from the pass |
| `ir` | string | Generated LLVM IR code |
| `error` | string | (On error) Error description |

### Supported Passes

| Pass | Type | Purpose | Output |
|------|------|---------|--------|
| `opcode-counter` | Function-level | Count instruction frequencies | Instruction counts per function |
---

### Python
```python
import requests

response = requests.post('http://localhost:5000/api/llvm-pass', json={
    "code": "int main() { return 42; }",
    "language": "c",
    "pass": "opcode-counter",
    "optimization": "-O0"
})

result = response.json()
print(result['pass_output'])
```

### JavaScript/Fetch
```javascript
const response = await fetch('http://localhost:5000/api/llvm-pass', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    code: 'int main() { return 42; }',
    language: 'c',
    pass: 'opcode-counter',
    optimization: '-O0'
  })
});

const result = await response.json();
console.log(result.pass_output);
```

### React Component Example
```jsx
import React, { useState } from 'react';

function LLVMPassAnalyzer() {
  const [code, setCode] = useState('int main() { return 0; }');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const analyzeCode = async () => {
    setLoading(true);
    try {
      const response = await fetch('http://localhost:5000/api/llvm-pass', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code,
          language: 'c',
          pass: 'opcode-counter',
          optimization: '-O0'
        })
      });

      const data = await response.json();
      setResult(data);
    } catch (error) {
      console.error('Error:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <textarea 
        value={code} 
        onChange={(e) => setCode(e.target.value)}
        rows={10} 
        cols={50}
      />
      <br />
      <button onClick={analyzeCode} disabled={loading}>
        {loading ? 'Analyzing...' : 'Analyze Code'}
      </button>

      {result && (
        <div>
          <h3>Analysis Results</h3>
          <pre>{result.pass_output || result.error}</pre>
        </div>
      )}
    </div>
  );
}

export default LLVMPassAnalyzer;
```

### cURL
```bash
curl -X POST http://localhost:5000/api/llvm-pass \
  -H "Content-Type: application/json" \
  -d '{
    "code": "int main() { return 42; }",
    "language": "c", 
    "pass": "opcode-counter",
    "optimization": "-O0"
  }'
```

---

## Understanding Opcode Counter Output

The `opcode-counter` pass analyzes LLVM IR and counts instruction types:

```
---------------------------------------------
Opcode Counts for Function: main
add : 1          # Addition operations
alloca : 3       # Memory allocations 
load : 2         # Memory loads
ret : 1          # Function returns
store : 3        # Memory stores
---------------------------------------------
```

### Parsing Results
```javascript
function parseOpcodeOutput(output) {
  const lines = output.split('\n');
  const counts = {};
  
  let inCounts = false;
  for (const line of lines) {
    if (line.includes('Opcode Counts for Function:')) {
      inCounts = true;
      continue;
    }
    if (line.includes('-----') && inCounts) break;
    
    if (inCounts && line.trim()) {
      const [opcode, count] = line.split(':').map(s => s.trim());
      if (opcode && count) {
        counts[opcode] = parseInt(count);
      }
    }
  }
  
  return counts;
}

// Usage
const result = parseOpcodeOutput(response.pass_output);
console.log(result); // { add: 1, alloca: 3, load: 2, ret: 1, store: 3 }
```

---

## Adding New LLVM Passes

### Step 1: Create Your Pass

Create a C++ file implementing your LLVM pass:

```cpp
// MyPass.cpp (New Pass Plugin API)
#include "llvm/IR/PassManager.h"
#include "llvm/IR/Function.h"
#include "llvm/Support/raw_ostream.h"

using namespace llvm;

struct MyPass : PassInfoMixin<MyPass> {
  PreservedAnalyses run(Function &F, FunctionAnalysisManager &AM) {
    outs() << "Running MyPass on function: " << F.getName() << "\n";
    // Your pass logic here
    return PreservedAnalyses::all();
  }
};

extern "C" llvm::PassPluginLibraryInfo LLVM_PLUGIN_EXPORT llvmGetPassPluginInfo() {
  return {
    LLVM_PLUGIN_API_VERSION, "MyPass", "0.1.0",
    [](PassBuilder &PB) {
      PB.registerPipelineParsingCallback(
        [](StringRef Name, FunctionPassManager &FPM,
           ArrayRef<PassBuilder::PipelineElement>) {
          if (Name == "my-pass") {
            FPM.addPass(MyPass());
            return true;
          }
          return false;
        });
    }
  };
}
```

### Step 2: Build the Pass

Create `CMakeLists.txt`:
```cmake
cmake_minimum_required(VERSION 3.20)
project(MyPass)

find_package(LLVM REQUIRED CONFIG)
add_definitions(${LLVM_DEFINITIONS})
include_directories(${LLVM_INCLUDE_DIRS})

add_library(MyPass SHARED MyPass.cpp)
target_compile_options(MyPass PRIVATE -fno-rtti)
```

Build:
```bash
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make
```

### Step 3: Update the Backend

**Update Dockerfile:**
```dockerfile
COPY path/to/libMyPass.so /opt/llvm-passes/
```

**Update app.py:**
```python
allowed_passes = ['opcode-counter', 'my-pass']
elif pass_name == 'my-pass':
    cmd_pass = (
        f"opt -load-pass-plugin=/opt/llvm-passes/libMyPass.so "
        f"-passes='function(my-pass)' -disable-output /io/code.ll"
    )
```

### Step 4: Test

```bash
docker build -t my-compiler-image .

curl -X POST http://localhost:5000/api/llvm-pass \
  -H "Content-Type: application/json" \
  -d '{"code":"int main(){return 0;}","pass":"my-pass"}'
```

---

## 🛡️ Security Features

✅ **Input Validation**
- Pass names validated against whitelist
- Prevents shell injection attacks
- Code inputs properly sanitized

✅ **Sandboxed Execution**
- Runs in isolated Docker container
- No network access allowed
- Memory limit: 256MB per request
- CPU quota enforced

✅ **Resource Management**
- Automatic cleanup of temporary files
- Proper permission handling (UID 1000:1000)
- Container removal after execution

---

## ⚡ Performance

| Metric | Value |
|--------|-------|
| Container startup | ~1-2 seconds |
| IR generation | ~100-500ms |
| Pass execution | <100ms (typical) |
| Total per request | ~2-3 seconds |
| Memory limit | 256MB (enforced) |

---

## Optimization Level Comparison

You can analyze how different optimization levels affect instruction counts:

```python
import requests

code = """
int fibonacci(int n) {
    if (n <= 1) return n;
    return fibonacci(n-1) + fibonacci(n-2);
}
"""

for opt in ['-O0', '-O1', '-O2', '-O3']:
    response = requests.post('http://localhost:5000/api/llvm-pass', json={
        'code': code,
        'pass': 'opcode-counter',
        'optimization': opt
    })
    
    result = response.json()
    # Parse and compare instruction counts
    print(f"{opt}: {result['pass_output']}")
```

---

## Troubleshooting

### "Could not load library" Error
**Problem:** `LLVM ERROR: Could not load library '/opt/llvm-passes/libOpcodeCounter.so'`

**Solution:**
1. Ensure the .so file was built successfully
2. Rebuild Docker image: `docker build -t my-compiler-image .`
3. Check LLVM version compatibility

### "unknown pass name" Error
**Problem:** `opt: unknown pass name 'my-pass'`

**Solutions:**
1. Add pass to `allowed_passes` list in app.py
2. Ensure pass handler is implemented
3. Verify .so file is correctly loaded

### "optnone attribute" Warning
**Problem:** `Skipping pass: OpcodeCounter on main due to optnone attribute`

**Solution:** This is already handled by `-Xclang -disable-O0-optnone` flag.

### Docker Build Issues
**Problem:** Long build times or failures

**Solutions:**
1. Ensure good internet connection (downloads LLVM packages)
2. Use `--no-cache` flag: `docker build --no-cache -t my-compiler-image .`
3. Check Docker daemon is running: `docker ps`

### Port 5000 In Use
**Problem:** `Address already in use`

**Solutions:**
1. Kill existing process: `lsof -i :5000` then `kill <PID>`
2. Use different port in app.py: `app.run(port=5001)`

---

## Backward Compatibility

 **All existing endpoints unchanged:**
- `/api/compile` - Original compilation endpoint
- All parameters and responses identical  
- No breaking changes to current API

---

## Testing

### Run All Tests
```bash
python3 integration_test.py
```

### Test Individual Components

**Test Docker Image:**
```bash
docker run --rm my-compiler-image opt --version
```

**Test .so File in Container:**
```bash
docker run --rm my-compiler-image ls -la /opt/llvm-passes/
```

**Test Basic Endpoint:**
```bash
curl http://localhost:5000/api/compile
```

**Test LLVM Pass Endpoint:**
```bash
curl -X POST http://localhost:5000/api/llvm-pass \
  -H "Content-Type: application/json" \
  -d '{"code":"int main(){return 0;}","pass":"opcode-counter"}'
```

---

## Requirements

- **Docker** (running daemon)
- **Python 3.8+**
- **LLVM 19** (for building passes)
- **Dependencies:** flask, flask-cors, docker, requests

Install Python dependencies:
```bash
pip install flask flask-cors docker requests
```

---

## Project Structure

```
OptiForge-Backend/
├── app.py                      # Main Flask app (UPDATED: +LLVM pass endpoint)
├── Dockerfile                  # Docker config (UPDATED: +LLVM 19, +.so file)
├── requirements.txt            # Python dependencies
├── integration_test.py         # Comprehensive test suite
├── quick_test.py              # Simple endpoint test
└── README.md                  # This file (complete documentation)

LLVM Pass Source:
../OptiForge/LLVMProject/OpcodeCounter/
├── CMakeLists.txt
├── OpcodeCounter.cpp
├── build/
│   └── libOpcodeCounter.so    # Plugin file (copied to Docker)
└── test.c
```

---

## Deployment Checklist

- [ ] **Build LLVM pass:** `make` in OpcodeCounter/build/
- [ ] **Build Docker image:** `docker build -t my-compiler-image .`
- [ ] **Install Python deps:** `pip install flask flask-cors docker requests`
- [ ] **Test integration:** `python3 integration_test.py` (should show 100% pass)
- [ ] **Start backend:** `python3 app.py`
- [ ] **Verify endpoint:** Test with curl or frontend
- [ ] **Update frontend:** Implement `/api/llvm-pass` calls
- [ ] **Deploy to production**

---

## Future Enhancements

Potential improvements:
- [ ] Support for module-level passes
- [ ] Pass result caching
- [ ] Structured JSON pass results
- [ ] Custom pass parameters
- [ ] Pass composition/chaining
- [ ] WebSocket support for long-running passes
- [ ] Performance benchmarking pass
- [ ] Real-time collaboration features

---

## Contributing

To add new LLVM passes:

1. Implement pass in C++ (see "Adding New LLVM Passes" section)
2. Build as shared object (.so) file
3. Update Dockerfile to copy .so file
4. Add pass handler to app.py
5. Update allowed_passes whitelist
6. Test thoroughly with integration_test.py
7. Update this README with new pass documentation

---

## License

Same as OptiForge project license.

---

## Support & Debugging

### Getting Help
1. **Run integration test:** `python3 integration_test.py`
2. **Check logs:** Backend console output shows detailed execution info
3. **Verify Docker:** `docker ps` and `docker logs <container-id>`
4. **Test components:** Use individual curl commands above

### Common Issues

**No output from pass:**
- Check pass is printing to stdout/stderr
- Verify pass is actually running (not skipped due to optnone)
- Use `-debug-pass-manager` flag for verbose output

**Performance issues:**
- First Docker build takes 3-5 minutes (LLVM installation)
- Subsequent builds are faster (Docker caching)
- Pass execution typically <100ms for small functions

### Debug Mode
Enable debug logging in app.py:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## Verification

To verify your setup is working:

```bash
# 1. Check all tests pass
python3 integration_test.py

# Expected: "All tests passed! Integration is working correctly."

# 2. Test live endpoint
curl -X POST http://localhost:5000/api/llvm-pass \
  -H "Content-Type: application/json" \
  -d '{
    "code": "int main() { int x = 42; return x; }",
    "language": "c",
    "pass": "opcode-counter"
  }'

# Expected: JSON response with pass_output containing instruction counts
```

---

**Your OptiForge backend now has full LLVM pass support! Users can analyze code at the IR level, understand compiler optimizations, and gain deep insights into program behavior.**

For questions or issues, review the troubleshooting section above or run the integration test for diagnostic information.
