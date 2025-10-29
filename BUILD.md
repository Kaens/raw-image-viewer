# Building instructions for Raw Image Viewer (C++)

### Install msys2

### update msys2 first (if you haven't recently)
`pacman -Syu`

### then install toolchain + development packages
`pacman -S --needed  mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-cmake mingw-w64-ucrt-x86_64-pkg-config mingw-w64-ucrt-x86_64-SDL2 mingw-w64-ucrt-x86_64-glew`

### Build commands:
`cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=MinSizeRel`
`cmake --build build -j4`
