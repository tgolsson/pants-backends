cmake_minimum_required(VERSION 3.20)
project(greet LANGUAGES C)

# A static library, which `cc_binary` and `cc_test` in ../BUILD link as well.
add_library(greet STATIC src/greet.c)
target_include_directories(greet PUBLIC include)

add_executable(hello src/main.c)
target_link_libraries(hello PRIVATE greet)
