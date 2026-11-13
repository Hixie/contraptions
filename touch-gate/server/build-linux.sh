#!/bin/sh
# Builds a static x86-64 Linux binary of touch-gate-server on macOS, using
# rustup's stable toolchain, Apple's clang for ring's C and assembly code,
# and LLVM's archiver and linker from the Rust toolchain. The binary is
# target/x86_64-unknown-linux-musl/release/touch-gate-server.
set -eu
cd "$(dirname "$0")"

target=x86_64-unknown-linux-musl
rustup target add --toolchain stable "$target"
rustup component add --toolchain stable llvm-tools
bin="$(dirname "$(rustup which --toolchain stable rustc)")"
host="$("$bin/rustc" -vV | sed -n 's/^host: //p')"

# The Rust toolchain's own rustc and cargo, ahead of any others on PATH.
export PATH="$bin:$PATH"
export CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER=rust-lld
export CC_x86_64_unknown_linux_musl=clang
export AR_x86_64_unknown_linux_musl="$("$bin/rustc" --print sysroot)/lib/rustlib/$host/bin/llvm-ar"
# There are no musl headers on macOS. ring needs only the compiler's own
# headers when RING_CORE_NOSTDLIBINC is set, and clang's stddef.h uses
# them unless __musl__ is defined.
export CFLAGS_x86_64_unknown_linux_musl="-ffreestanding -U__musl__ -DRING_CORE_NOSTDLIBINC=1"

cargo build --release --target "$target"
