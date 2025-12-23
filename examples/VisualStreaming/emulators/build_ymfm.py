"""Build script for ymfm Python bindings."""

import subprocess
import sys
import os

def build():
    # Get paths
    this_dir = os.path.dirname(os.path.abspath(__file__))
    ymfm_src = os.path.join(this_dir, "ymfm_src", "src")
    binding_cpp = os.path.join(this_dir, "ymfm_binding.cpp")

    # Get pybind11 include path
    try:
        import pybind11
        pybind11_include = pybind11.get_include()
    except ImportError:
        print("ERROR: pybind11 not installed. Run: pip install pybind11")
        return False

    # Get Python include and lib paths
    python_include = os.path.join(sys.prefix, "include")
    python_libs = os.path.join(sys.prefix, "libs")

    # Output file
    suffix = ".cp312-win_amd64.pyd" if sys.platform == "win32" else ".so"
    output = os.path.join(this_dir, f"_ymfm{suffix}")

    print(f"Building ymfm binding...")
    print(f"  ymfm source: {ymfm_src}")
    print(f"  pybind11: {pybind11_include}")
    print(f"  Output: {output}")

    if sys.platform == "win32":
        # Windows: Use cl.exe (MSVC)
        cmd = [
            "cl", "/O2", "/EHsc", "/std:c++17", "/LD",
            f"/I{ymfm_src}",
            f"/I{pybind11_include}",
            f"/I{python_include}",
            binding_cpp,
            f"/Fe:{output}",
            f"/link", f"/LIBPATH:{python_libs}",
        ]
    else:
        # Linux/Mac: Use g++
        cmd = [
            "g++", "-O3", "-shared", "-std=c++17", "-fPIC",
            f"-I{ymfm_src}",
            f"-I{pybind11_include}",
            f"-I{python_include}",
            binding_cpp,
            "-o", output,
            f"-L{python_libs}",
            f"-lpython{sys.version_info.major}.{sys.version_info.minor}",
        ]

    print(f"Command: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, cwd=this_dir, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Build failed!")
            print(result.stdout)
            print(result.stderr)
            return False
        print(f"Build successful: {output}")
        return True
    except FileNotFoundError as e:
        print(f"Compiler not found: {e}")
        print("Make sure you have Visual Studio Build Tools (Windows) or g++ (Linux/Mac)")
        return False

if __name__ == "__main__":
    success = build()
    sys.exit(0 if success else 1)
