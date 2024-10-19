#!/bin/bash

# Добавляем данные из настроек
source ../settings.sh

# Начало отсчета времени выполнения скрипта
start_time=$(date +%s)

# Удаление каталога "out", если он существует
rm -rf out

# Основной каталог
MAINPATH=/home/timisong # измените, если необходимо

# Каталог ядра
KERNEL_DIR=$MAINPATH/kernel
KERNEL_PATH=$KERNEL_DIR/kernel_xiaomi_sm8250

git log $LAST..HEAD > ../log.txt
BRANCH=$(git branch --show-current)

# Каталоги компиляторов
CLANG19_DIR=$KERNEL_DIR/clang19
ANDROID_PREBUILTS_GCC_ARM_DIR=$KERNEL_DIR/android_prebuilts_gcc_linux-x86_arm_arm-linux-androideabi-4.9
ANDROID_PREBUILTS_GCC_AARCH64_DIR=$KERNEL_DIR/android_prebuilts_gcc_linux-x86_aarch64_aarch64-linux-android-4.9

# Проверка и клонирование, если необходимо
check_and_clone() {
    local dir=$1
    local repo=$2

    if [ ! -d "$dir" ]; then
        echo "Папка $dir не существует. Клонирование $repo."
        git clone $repo $dir
    fi
}

check_and_wget() {
    local dir=$1
    local repo=$2

    if [ ! -d "$dir" ]; then
        echo "Папка $dir не существует. Клонирование $repo."
        mkdir $dir
        cd $dir
        wget $repo
        tar -zxvf Clang-19.0.0git-20240625.tar.gz
        rm -rf Clang-19.0.0git-20240625.tar.gz
        cd ../kernel_xiaomi_sm8250
    fi
}

# Клонирование инструментов компиляции, если они не существуют
check_and_wget $CLANG19_DIR https://github.com/ZyCromerZ/Clang/releases/download/19.0.0git-20240625-release/Clang-19.0.0git-20240625.tar.gz
check_and_clone $ANDROID_PREBUILTS_GCC_ARM_DIR https://github.com/LineageOS/android_prebuilts_gcc_linux-x86_arm_arm-linux-androideabi-4.9
check_and_clone $ANDROID_PREBUILTS_GCC_AARCH64_DIR https://github.com/LineageOS/android_prebuilts_gcc_linux-x86_aarch64_aarch64-linux-android-4.9

# Установка переменных PATH
PATH=$CLANG19_DIR/bin:$ANDROID_PREBUILTS_GCC_AARCH64_DIR/bin:$ANDROID_PREBUILTS_GCC_ARM_DIR/bin:$PATH
export PATH
export ARCH=arm64

# Каталог для сборки MagicTime
MAGIC_TIME_DIR="$KERNEL_DIR/MagicTime"

# Создание каталога MagicTime, если его нет
if [ ! -d "$MAGIC_TIME_DIR" ]; then
    mkdir -p "$MAGIC_TIME_DIR"
    
    # Проверка и клонирование Anykernel, если MagicTime не существует
    if [ ! -d "$MAGIC_TIME_DIR/Anykernel" ]; then
        git clone https://github.com/TIMISONG-dev/Anykernel.git "$MAGIC_TIME_DIR/Anykernel"
        
        # Перемещение всех файлов из Anykernel в MagicTime
        mv "$MAGIC_TIME_DIR/Anykernel/"* "$MAGIC_TIME_DIR/"
        
        # Удаление папки Anykernel
        rm -rf "$MAGIC_TIME_DIR/Anykernel"
    fi
else
    # Если папка MagicTime существует, проверить наличие .git и удалить, если есть
    if [ -d "$MAGIC_TIME_DIR/.git" ]; then
        rm -rf "$MAGIC_TIME_DIR/.git"
    fi
fi

# Экспорт переменных среды
export IMGPATH="$MAGIC_TIME_DIR/Image"
export DTBPATH="$MAGIC_TIME_DIR/dtb"
export DTBOPATH="$MAGIC_TIME_DIR/dtbo.img"
export CROSS_COMPILE="aarch64-linux-gnu-"
export CROSS_COMPILE_COMPAT="arm-linux-gnueabi-"
export KBUILD_BUILD_USER="TIMISONG"
export KBUILD_BUILD_HOST="timisong-dev"
export MODEL="alioth"

# Запись времени сборки
MAGIC_BUILD_DATE=$(date '+%Y-%m-%d_%H-%M-%S')

# Каталог для результатов сборки
output_dir=out

# Конфигурация ядра
make O="$output_dir" \
            alioth_defconfig \
            vendor/xiaomi/sm8250-common.config

    # Компиляция ядра
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
                

# Предполагается, что переменная DTS установлена ранее в скрипте
find $DTS -name '*.dtb' -exec cat {} + > $DTBPATH
find $DTS -name 'Image' -exec cat {} + > $IMGPATH
find $DTS -name 'dtbo.img' -exec cat {} + > $DTBOPATH

# Завершение отсчета времени выполнения скрипта
end_time=$(date +%s)
elapsed_time=$((end_time - start_time))

cd "$KERNEL_PATH"

# Проверка успешности сборки
if grep -q -E "Ошибка 2|Error 2" error.log; then
    cd "$KERNEL_PATH"
    echo "Ошибка: Сборка завершилась с ошибкой"

    curl -s -X POST https://api.telegram.org/bot$TGTOKEN/sendMessage \
    -d chat_id="@magictimekernel" \
    -d text="Ошибка в компиляции!" \
    -d message_thread_id="38153"

    curl -s -X POST "https://api.telegram.org/bot$TGTOKEN/sendDocument?chat_id=@magictimekernel" \
    -F document=@"./error.log" \
    -F message_thread_id="38153"
else
    echo "Общее время выполнения: $elapsed_time секунд"
    # Перемещение в каталог MagicTime и создание архива
    cd "$MAGIC_TIME_DIR"
    7z a -mx9 MagicTime-$MODEL-$MAGIC_BUILD_DATE.zip * -x!*.zip
    
    curl -s -X POST https://api.telegram.org/bot$TGTOKEN/sendMessage \
    -d chat_id="@magictimekernel" \
    -d text="Компиляция завершилась успешно! Время выполнения: $elapsed_time секунд" \
    -d message_thread_id="38153"

    curl -s -X POST "https://api.telegram.org/bot$TGTOKEN/sendDocument?chat_id=@magictimekernel" \
    -F document=@"./MagicTime-$MODEL-$MAGIC_BUILD_DATE.zip" \
    -F caption="MagicTime ${VERSION}${PREFIX}${BUILD} (${BUILD_TYPE})" \
    -F message_thread_id="38153"
    
    curl -s -X POST "https://api.telegram.org/bot$TGTOKEN/sendDocument?chat_id=@magictimekernel" \
    -F document=@"../log.txt" \
    -F caption="Latest changes" \
    -F message_thread_id="38153"

    rm -rf MagicTime-$MODEL-$MAGIC_BUILD_DATE.zip

    BUILD=$((BUILD + 1))

    cd "$KERNEL_PATH"
    LAST=$(git log -1 --format=%H)

    sed -i "s/LAST=.*/LAST=$LAST/" ../settings.sh
    sed -i "s/BUILD=.*/BUILD=$BUILD/" ../settings.sh
fi
