# Native Android (arm64-v8a) build of REDRIVER2.
#
# Standalone build (no Gradle), useful for checking the NDK compile:
#   cmake -S platform/Android -B build_android -G Ninja \
#     -DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK/build/cmake/android.toolchain.cmake \
#     -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26
#   cmake --build build_android
#
# Dependencies are expected in src_rebuild/dependencies (see windows_dev_prepare.ps1).
cmake_minimum_required(VERSION 3.22)
project(REDRIVER2 C CXX)

set(SRC_DIR "${CMAKE_CURRENT_LIST_DIR}/../..")
set(DEP_DIR "${SRC_DIR}/dependencies")
set(GAME_REGION "NTSC_VERSION" CACHE STRING "NTSC_VERSION or PAL_VERSION")

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Android 15+ devices may use 16 KB pages, native libraries have to be aligned for that
# (the default only since NDK r28). Set before the dependencies so they get it too.
string(APPEND CMAKE_SHARED_LINKER_FLAGS " -Wl,-z,max-page-size=16384")
string(APPEND CMAKE_EXE_LINKER_FLAGS " -Wl,-z,max-page-size=16384")

# the decompiled code relies on lax C++ rules (see the premake Android settings)
# 'char' is signed on x86 and on the PSX's MIPS but unsigned on ARM, the game relies on signed
set(RR_COMPILE_OPTS
    -fsigned-char
    -fpermissive -fexceptions -pthread
    -Wno-c++11-narrowing -Wno-constant-conversion -Wno-writable-strings -Wno-unused-value
    -Wno-switch -Wno-shift-op-parentheses -Wno-parentheses -Wno-format -Wno-format-security)

# ---- SDL2 ----
set(SDL_STATIC OFF CACHE BOOL "" FORCE)
set(SDL_SHARED ON CACHE BOOL "" FORCE)
set(SDL_TEST OFF CACHE BOOL "" FORCE)
add_subdirectory("${DEP_DIR}/SDL2-2.32.10" SDL2 EXCLUDE_FROM_ALL)

# ---- OpenAL Soft ----
set(LIBTYPE SHARED CACHE STRING "" FORCE)
set(ALSOFT_UTILS OFF CACHE BOOL "" FORCE)
set(ALSOFT_EXAMPLES OFF CACHE BOOL "" FORCE)
set(ALSOFT_TESTS OFF CACHE BOOL "" FORCE)
set(ALSOFT_INSTALL OFF CACHE BOOL "" FORCE)
add_subdirectory("${DEP_DIR}/openal-soft-1.23.1" openal EXCLUDE_FROM_ALL)

# ---- libjpeg (IJG 9d) ----
set(JPEG_DIR "${DEP_DIR}/jpeg-9d")
if(NOT EXISTS "${JPEG_DIR}/jconfig.h")
    configure_file("${JPEG_DIR}/jconfig.txt" "${CMAKE_BINARY_DIR}/jpeg_inc/jconfig.h" COPYONLY)
endif()
set(JPEG_SRCS
    jaricom jcapimin jcapistd jcarith jccoefct jccolor jcdctmgr jchuff jcinit jcmainct jcmarker
    jcmaster jcomapi jcparam jcprepct jcsample jctrans jdapimin jdapistd jdarith jdatadst jdatasrc
    jdcoefct jdcolor jddctmgr jdhuff jdinput jdmainct jdmarker jdmaster jdmerge jdpostct jdsample
    jdtrans jerror jfdctflt jfdctfst jfdctint jidctflt jidctfst jidctint jmemmgr jmemnobs jquant1
    jquant2 jutils)
list(TRANSFORM JPEG_SRCS PREPEND "${JPEG_DIR}/")
list(TRANSFORM JPEG_SRCS APPEND ".c")
add_library(jpeg STATIC ${JPEG_SRCS})
target_include_directories(jpeg PUBLIC "${JPEG_DIR}" "${CMAKE_BINARY_DIR}/jpeg_inc")

# ---- PsyCross (PSX emulation layer) ----
file(GLOB_RECURSE PSYCROSS_C CONFIGURE_DEPENDS "${SRC_DIR}/PsyCross/*.c")
file(GLOB_RECURSE PSYCROSS_CPP CONFIGURE_DEPENDS "${SRC_DIR}/PsyCross/*.cpp")
list(FILTER PSYCROSS_C EXCLUDE REGEX "glad\.c$")   # desktop GL loader, not used with GLES
add_library(PsyCross STATIC ${PSYCROSS_C} ${PSYCROSS_CPP})
target_compile_definitions(PsyCross PUBLIC ${GAME_REGION})
target_include_directories(PsyCross PUBLIC
    "${SRC_DIR}/PsyCross/include" "${SRC_DIR}/PsyCross/include/psx")
target_include_directories(PsyCross PRIVATE
    "${DEP_DIR}/SDL2-2.32.10/include" "${DEP_DIR}/openal-soft-1.23.1/include")
target_compile_options(PsyCross PRIVATE ${RR_COMPILE_OPTS})
target_link_libraries(PsyCross PUBLIC SDL2 OpenAL GLESv3 log android)

# ---- Game (libmain.so, loaded by SDL's Java activity) ----
file(GLOB_RECURSE GAME_C CONFIGURE_DEPENDS "${SRC_DIR}/Game/*.c")
file(GLOB_RECURSE GAME_CPP CONFIGURE_DEPENDS "${SRC_DIR}/Game/*.cpp" "${SRC_DIR}/utils/*.cpp")
file(GLOB_RECURSE UTILS_C CONFIGURE_DEPENDS "${SRC_DIR}/utils/*.c")
set_source_files_properties(${GAME_C} PROPERTIES LANGUAGE CXX)   # matches premake "compileas C++"

add_library(main SHARED ${GAME_C} ${GAME_CPP} ${UTILS_C} "${SRC_DIR}/redriver2_psxpc.cpp")
target_include_directories(main PRIVATE "${SRC_DIR}/Game" "${DEP_DIR}/SDL2-2.32.10/include")
target_compile_definitions(main PRIVATE ${GAME_REGION} "BUILD_CONFIGURATION_STRING=\"Release\"")
target_compile_options(main PRIVATE ${RR_COMPILE_OPTS})
target_link_libraries(main PRIVATE PsyCross jpeg)
