#!/bin/bash

source ../settings.sh

#
#   settings.sh (example)
#
# export VERSION="1.x.x"
# export BUILD=1
# export PREFIX="e"
# export DESC="description"
# export DEVICE="alioth"
# export TGTOKEN=bot_id
# export LAST=last commit hash for generation changelog
# export TYPE="test or early"
# export LEVEL=1
# export EXTRA=""
# export SHAK=hash commit for squash revert KSU
# export SHAB=hash commit for 5k battery
#

rm -rf out

MAIN=/home/timisong

KERNEL=$PWD

CLANG=$MAIN/clang
GCC_ARM=$MAIN/arm-linux-androideabi-4.9
GCC_AARCH64=$MAIN/aarch64-linux-android-4.9

check_and_clone() {
    local dir=$1
    local repo=$2
    local name=$3

    if [ ! -d $dir ]; then
        echo Папка $dir не существует. Клонирование $repo
        cd $MAIN
        git clone $repo $name
    fi
}

check_and_wget() {
    local dir=$1
    local repo=$2

    if [ ! -d $dir ]; then
        echo Папка $dir не существует. Клонирование $repo
        mkdir $dir
        cd $dir
        wget -O clang.tar.gz $repo
        tar -zxvf clang.tar.gz
        rm -rf clang.tar.gz
        cd ../kernel_xiaomi_sm8250
    fi
}

build() {
    START=$(date +%s)
    git log $LAST..HEAD > ../changelog.txt
    BRANCH=$(git branch --show-current)

    MAGICTIME=$MAIN/MagicTime-$DEVICE

    if [ $DEVICE = pipa ]; then
        ANYKERNEL_LINK=https://github.com/TIMISONG-dev/MagicTime-pipa
    else
        ANYKERNEL_LINK=https://github.com/TIMISONG-dev/MagicTime-alioth
    fi

    if [ ! -d $MAGICTIME ]; then
        mkdir -p $MAGICTIME

        if [ ! -d $MAGICTIME/Anykernel ]; then
            git clone $ANYKERNEL_LINK $MAGICTIME/Anykernel

            mv $MAGICTIME/Anykernel/* $MAGICTIME/

            rm -rf $MAGICTIME/Anykernel
        fi
    else
        if [ -d $MAGICTIME/.git ]; then
            rm -rf $MAGICTIME/.git
        fi
    fi

    if [ $DEVICE = pipa ]; then
        IMG=$MAGICTIME/kernels/Image
        DTB=$MAGICTIME/kernels/dtb
        DTBO=$MAGICTIME/kernels/dtbo.img
    else
        IMG=$MAGICTIME/Image
        DTB=$MAGICTIME/dtb
        DTBO=$MAGICTIME/dtbo.img
    fi

    make O="$OUT" \
            ${DEVICE}_defconfig \
            vendor/xiaomi/magictime-common.config

    # Компиляция ядра
    make -j $(nproc) \
                O="$OUT" \
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
                V=$VERBOSE 2>&1 | tee build.log

    find $DTS -name '*.dtb' -exec cat {} + > $DTB
    find $DTS -name 'Image' -exec cat {} + > $IMG
    find $DTS -name 'dtbo.img' -exec cat {} + > $DTBO

    END=$(date +%s)
    ELAPSED=$((END - START))

    if grep -q -E "Ошибка 2|Error 2" build.log; then
        echo Ошибка: Сборка завершилась с ошибкой

        curl -s -X POST https://api.telegram.org/bot$TGTOKEN/sendMessage \
        -d chat_id=@magictimekernel \
        -d text="Ошибка в компиляции!" \
        -d message_thread_id=38153

        curl -s -X POST https://api.telegram.org/bot$TGTOKEN/sendDocument?chat_id=@magictimekernel \
        -F document=@./build.log \
        -F message_thread_id=38153

        curl -s -X POST https://api.telegram.org/bot$TGTOKEN/sendDocument?chat_id=@magictimekernel \
        -F document=@../changelog.txt \
        -F message_thread_id=38153
    else
        echo Общее время выполнения: $ELAPSED секунд

        cd $MAGICTIME
        
        if [ "$TYPE" = "test" ]; then
            7z a -mx9 MagicTime-$DEVICE-$FILE.zip * -x!*.zip
        else
            7z a -mx9 MagicTime-$DEVICE-$BUILD_DATE.zip * -x!*.zip
        fi
        
        curl -s -X POST https://api.telegram.org/bot$TGTOKEN/sendMessage \
        -d chat_id=@magictimekernel \
        -d text="Компиляция завершилась успешно! Время выполнения: $ELAPSED секунд" \
        -d message_thread_id=38153

        if [ "$TYPE" = "test" ]; then
            curl -s -X POST https://api.telegram.org/bot$TGTOKEN/sendDocument?chat_id=@magictimekernel \
            -F document=@./MagicTime-$DEVICE-$FILE.zip \
            -F caption="MagicTime ${VERSION}${PREFIX}${BUILD} (${DESC}) branch: ${BRANCH}" \
            -F message_thread_id=38153
        else
            curl -s -X POST https://api.telegram.org/bot$TGTOKEN/sendDocument?chat_id=@magictimekernel \
            -F document=@./MagicTime-$DEVICE-$BUILD_DATE.zip \
            -F caption="MagicTime ${VERSION}${PREFIX}${BUILD} (${DESC}) branch: ${BRANCH}" \
            -F message_thread_id=38153
        fi
        
        curl -s -X POST https://api.telegram.org/bot$TGTOKEN/sendDocument?chat_id=@magictimekernel \
        -F document=@../changelog.txt \
        -F caption="Latest changes" \
        -F message_thread_id=38153

        if [ "$TYPE" = "test" ]; then
            rm -rf MagicTime-$DEVICE-$FILE.zip
        else
            rm -rf MagicTime-$DEVICE-$BUILD_DATE.zip
        fi

        BUILD=$((BUILD + 1))

        cd $KERNEL
        LAST=$(git log -1 --format=%H)

        sed -i "s/LAST=.*/LAST=$LAST/" ../settings.sh
        sed -i "s/BUILD=.*/BUILD=$BUILD/" ../settings.sh
    fi
}

check_and_wget $CLANG \
    https://github.com/ZyCromerZ/Clang/releases/download/20.0.0git-20250129-release/Clang-20.0.0git-20250129.tar.gz
check_and_clone $GCC_ARM \
    https://github.com/LineageOS/android_prebuilts_gcc_linux-x86_arm_arm-linux-androideabi-4.9 \
        arm-linux-androideabi-4.9
check_and_clone $GCC_AARCH64 \
    https://github.com/LineageOS/android_prebuilts_gcc_linux-x86_aarch64_aarch64-linux-android-4.9 \
        aarch64-linux-android-4.9

export PATH=$CLANG/bin:$GCC_AARCH64/bin:$GCC_ARM/bin:$PATH
export ARCH=arm64
export CROSS_COMPILE=aarch64-linux-gnu-
export CROSS_COMPILE_COMPAT=arm-linux-gnueabi-
export KBUILD_BUILD_USER=TIMISONG
export KBUILD_BUILD_HOST=timisong-dev

BUILD_DATE=$(date '+%Y-%m-%d_%H-%M-%S')

OUT=out

# Early build
if [ $LEVEL = 1 ] && [ $TYPE = early ]; then
    build
    clear
fi

# Test builds
if [ "$TYPE" = "test" ]; then
    # Format: "DEVICE:BRANCH:MOD_KSU:MOD_BATTERY:DESC:FILE"
    CONFIGS=(
        "alioth:magictime-new:ksu:stk:POCO F3 AOSP:AOSP-KSU"
        "pipa:magictime-new:ksu:stk:Mi Pad 6 AOSP:AOSP-KSU"
	    "apollo:magictime-new:ksu:stk:Mi 10T AOSP:AOSP-KSU"
        "alioth:magictime-new:ksu:5k:POCO F3 AOSP 5k battery:AOSP-KSU-5K"
        "alioth:magictime-new:no_ksu:stk:POCO F3 AOSP without KSU:AOSP-NONKSU"
        "pipa:magictime-new:no_ksu:stk:Mi Pad 6 AOSP without KSU:AOSP-NONKSU"
	    "apollo:magictime-new:no_ksu:stk:Mi 10T AOSP without KSU:AOSP-NONKSU"
        "alioth:magictime-new:no_ksu:5k:POCO F3 AOSP without KSU 5k battery:AOSP-NONKSU-5K"
        
        "alioth:magictime-miui:ksu:stk:POCO F3 MIUI:MIUI-KSU"
        "pipa:magictime-miui:ksu:stk:Mi Pad 6 MIUI:MIUI-KSU"
	    "apollo:magictime-miui:ksu:stk:Mi 10T MIUI:MIUI-KSU"
        "alioth:magictime-miui:ksu:5k:POCO F3 MIUI 5k battery:MIUI-KSU-5K"
        "alioth:magictime-miui:no_ksu:stk:POCO F3 MIUI without KSU:MIUI-NONKSU"
        "pipa:magictime-miui:no_ksu:stk:Mi Pad 6 MIUI without KSU:MIUI-NONKSU"
	    "apollo:magictime-miui:no_ksu:stk:Mi 10T MIUI without KSU:MIUI-NONKSU"
        "alioth:magictime-miui:no_ksu:5k:POCO F3 MIUI without KSU 5k battery:MIUI-NONKSU-5K"
    )

    while true; do
        IDX=$((LEVEL - 1))

        if [ "$IDX" -ge "${#CONFIGS[@]}" ]; then
            LEVEL=1
            EXTRA=""
            sed -i "s/LEVEL=.*/LEVEL=1/" ../settings.sh
            sed -i "s/EXTRA=.*/EXTRA=\"\"/ " ../settings.sh
            git checkout magictime-new >/dev/null 2>&1
            git reset --hard origin/magictime-new >/dev/null 2>&1
            clear
            exit 0
        fi

        IFS=':' read -r DEVICE BRANCH MOD_KSU MOD_BATTERY DESC FILE <<< "${CONFIGS[$IDX]}"

        echo "=== Сборка уровня $LEVEL / ${#CONFIGS[@]}: $DESC ==="

        git checkout "$BRANCH" >/dev/null 2>&1
        git fetch origin "$BRANCH" >/dev/null 2>&1 || true
        git reset --hard "origin/$BRANCH" >/dev/null 2>&1

        if [ "$MOD_BATTERY" = "5k" ]; then
            git cherry-pick --no-commit "$SHAB" >/dev/null 2>&1
            if [ $? -ne 0 ]; then
                git cherry-pick --abort >/dev/null 2>&1
                exit 1
            fi
        fi

        if [ "$MOD_KSU" = "no_ksu" ]; then
            git cherry-pick --no-commit "$SHAK" >/dev/null 2>&1
            if [ $? -ne 0 ]; then
                git cherry-pick --abort >/dev/null 2>&1
                exit 1
            fi
        fi

        if [ "$EXTRA" = "!4" ]; then
            git cherry-pick "$SHAK" || true
        elif [ "$EXTRA" = "!10" ]; then
            git cherry-pick "$SHAK" || true
        fi

        if build; then
            NEXT_LEVEL=$((LEVEL + 1))
            sed -i "s/LEVEL=.*/LEVEL=$NEXT_LEVEL/" ../settings.sh
            LEVEL=$NEXT_LEVEL
            clear
        else
            echo "Ошибка сборки на уровне $LEVEL ($DESC)"
            exit 1
        fi
    done
fi
