cmake_minimum_required(VERSION 3.10)
project(OpcodeCounter)

find_package(LLVM REQUIRED CONFIG)

list(APPEND CMAKE_MODULE_PATH "${LLVM_CMAKE_DIR}")
include(AddLLVM)

add_definitions(${LLVM_DEFINITIONS})
include_directories(${LLVM_INCLUDE_DIRS})

add_library(OpcodeCounter MODULE OpcodeCounter.cpp)

set_target_properties(OpcodeCounter PROPERTIES 
    CXX_STANDARD 17 
    CXX_STANDARD_REQUIRED ON
)
