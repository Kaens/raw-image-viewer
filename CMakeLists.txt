cmake_minimum_required(VERSION 4.0)
project(rawviewer_sdl_imgui LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE OFF)

# Prefer Release by default if not set
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# SDL2 (installed via pacman in MSYS2 UCRT64)
find_package(SDL2 REQUIRED)
if(NOT TARGET SDL2::SDL2)
  # On some msys2 installs find_package yields SDL2_LIBRARIES instead
  include_directories(${SDL2_INCLUDE_DIRS})
  set(SDL2_LIBRARIES ${SDL2_LIBRARIES})
else()
  message(STATUS "Using SDL2 target from package")
endif()

# FetchContent for ImGui and stb_image_write
include(FetchContent)
set(FETCHCONTENT_BASE_DIR ${CMAKE_SOURCE_DIR}/third_party)

# Dear ImGui
FetchContent_Declare(
  imgui
  GIT_REPOSITORY https://github.com/ocornut/imgui.git
  GIT_TAG docking
)
FetchContent_MakeAvailable(imgui)

# stb (for saving PNG)
FetchContent_Declare(
  stb
  GIT_REPOSITORY https://github.com/nothings/stb.git
  GIT_TAG master
)
FetchContent_MakeAvailable(stb)

# Source
add_executable(rawviewer src/main.cpp)

# ImGui sources for backends
target_sources(rawviewer
  PRIVATE
    ${imgui_SOURCE_DIR}/backends/imgui_impl_sdl2.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_opengl3.cpp
    ${imgui_SOURCE_DIR}/imgui.cpp
    ${imgui_SOURCE_DIR}/imgui_draw.cpp
    ${imgui_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_SOURCE_DIR}/imgui_widgets.cpp
    ${imgui_SOURCE_DIR}/misc/cpp/imgui_stdlib.cpp
)

# glew source
find_package(GLEW REQUIRED)
target_link_libraries(rawviewer PRIVATE GLEW::GLEW)

#NFD source
FetchContent_Declare(
  nativefiledialog
  GIT_REPOSITORY https://github.com/btzy/nativefiledialog-extended.git
  GIT_TAG master
)
FetchContent_MakeAvailable(nativefiledialog)

target_link_libraries(rawviewer PRIVATE nfd)

# include paths
target_include_directories(rawviewer PRIVATE
  ${imgui_SOURCE_DIR}
  ${imgui_SOURCE_DIR}/backends
  ${nativefiledialog_SOURCE_DIR}/src/include
  ${stb_SOURCE_DIR}
)

# Link SDL2 (prefer target if available)
if(TARGET SDL2::SDL2)
  target_link_libraries(rawviewer PRIVATE SDL2::SDL2)
  if (WIN32)
    target_link_libraries(rawviewer PRIVATE SDL2::SDL2main)
  endif()
else()
  # fallback to pkg-config variables
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(SDL2 REQUIRED sdl2)
  target_include_directories(rawviewer PRIVATE ${SDL2_INCLUDE_DIRS})
  target_link_libraries(rawviewer PRIVATE ${SDL2_LIBRARIES})
endif()

# Link OpenGL (platform), plus a couple more Windows things
if (WIN32)
  target_link_libraries(rawviewer PRIVATE GLEW::GLEW)
else()
  find_package(OpenGL REQUIRED)
  target_link_libraries(rawviewer PRIVATE GLEW::GLEW)
endif()

# compile flags for MSYS/MinGW: ensure linking with static runtime is not forced
if(WIN32)
    target_link_options(rawviewer PRIVATE
      -Wl,-subsystem,console
      -lSDL2main -lSDL2 -lopengl32 -lkernel32 -luser32 -lgdi32 -lmsvcrt
    )
endif()
if (MINGW)
  target_compile_options(rawviewer PRIVATE
    -Wall
    -Wextra
    -Wno-unused-parameter
    -Os
    -s
    -ffunction-sections
    -fdata-sections
    -fno-align-functions
    -fno-align-jumps
    -fno-align-loops
    -fno-align-labels
    -fno-unroll-loops
    -fno-inline-functions
  )
  target_link_options(rawviewer PRIVATE
    -Wl,--gc-sections
    -Wl,--dynamicbase
    -Wl,--high-entropy-va
    -Wl,--as-needed
  )
endif()

# Define STB implementation symbol so we can use stb_image_write
target_compile_definitions(rawviewer PRIVATE STB_IMAGE_WRITE_IMPLEMENTATION)
