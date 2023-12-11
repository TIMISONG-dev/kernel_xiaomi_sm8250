CLANG=/home/timisong/kernel/snapdragon-clang/bin/
GCC32=/home/timisong/kernel/android_prebuilts_gcc_linux-x86_arm_arm-linux-androideabi-4.9/bin/
GCC64=/home/timisong/kernel/android_prebuilts_gcc_linux-x86_aarch64_aarch64-linux-android-4.9/bin/

PATH=$CLANG:$GCC64:$GCC32:$PATH

export PATH
export ARCH=arm64
export IMGPATH
export DTBPATH

export CLANG_TRIPLE="aarch64-linux-gnu-"
export CROSS_COMPILE="aarch64-linux-gnu-"
export CROSS_COMPILE_ARM32="arm-linux-gnueabi-"
export KBUILD_BUILD_USER="TIMISONG"
export KBUILD_BUILD_HOST="timisong-dev"

IMGPATH="/home/timisong/kernel/MagicTime/Image.gz"
DTBPATH="/home/timisong/kernel/MagicTime/dtb"

CLANG_TRIPLE="aarch64-linux-gnu-"
CROSS_COMPILE="aarch64-linux-gnu-"
CROSS_COMPILE_ARM32="arm-linux-gnueabi-"
MAGIC_BUILD_DATE=$(date '+%Y-%m-%d_%H-%M-%S')

output_dir=out
make O="$output_dir" \
            vendor/kona-perf_defconfig \
            vendor/xiaomi/sm8250-common.config \
            vendor/xiaomi/alioth.config

make -j $(nproc) \
            O="$output_dir" \
            CC="ccache clang" \
            HOSTCC=gcc \
            LD=ld.lld \
            AS=llvm-as \
            AR=llvm-ar \
            NM=llvm-nm \
            OBJCOPY=llvm-objcopy \
            OBJDUMP=llvm-objdump \
            STRIP=llvm-strip \
            LLVM=1 \
            LLVM_IAS=1 \
            V=$VERBOSE 2>&1 | tee error.log

find $DTS -name '*.dtb' -exec cat {} + > $DTBPATH
find $DTS -name 'Image.gz' -exec cat {} + > $IMGPATH

cd ../MagicTime
7z a -mx9 MagicTime_$MAGIC_BUILD_DATE.zip

cd ../kernel_xiaomi_sm8250
rm -rf out