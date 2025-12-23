"""Setup script for ymfm Python bindings."""

from setuptools import setup, Extension
import pybind11
import os

this_dir = os.path.dirname(os.path.abspath(__file__))
ymfm_src = os.path.join(this_dir, "ymfm_src", "src")

# Change to this directory so relative paths work
os.chdir(this_dir)

ext = Extension(
    "_ymfm",
    sources=[os.path.join(this_dir, "ymfm_binding.cpp")],
    include_dirs=[
        ymfm_src,
        pybind11.get_include(),
    ],
    language="c++",
    extra_compile_args=["/std:c++17", "/O2", "/EHsc"] if os.name == "nt" else ["-std=c++17", "-O3"],
)

setup(
    name="_ymfm",
    version="1.0",
    ext_modules=[ext],
    script_args=["build_ext", "--inplace"],
)
