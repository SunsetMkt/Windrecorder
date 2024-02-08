import datetime

# https://steam.oxxostudio.tw/category/python/library/threading.html
import os
import subprocess
import sys
import threading
import time
from os import getpid

import numpy as np
import pyautogui

from windrecorder import (
    file_utils,
    ocr_manager,
    record,
    record_wintitle,
    utils,
    wordcloud,
)
from windrecorder.config import config
from windrecorder.exceptions import LockExistsException
from windrecorder.lock import FileLock

# 全局状态变量
monitor_idle_minutes = 0
last_screenshot_array = None
idle_maintain_time_gap = datetime.timedelta(hours=8)  # 与上次闲时维护至少相隔
idle_maintaining_in_process = False  # 维护中的锁

last_idle_maintain_time = datetime.datetime.now()

try:
    # 读取之前闲时维护的时间
    with open(config.last_idle_maintain_file_path, "r", encoding="utf-8") as f:
        time_read = f.read()
        last_idle_maintain_time = datetime.datetime.strptime(time_read, "%Y-%m-%d_%H-%M-%S")
except FileNotFoundError:
    file_utils.ensure_dir("cache")
    with open(config.last_idle_maintain_file_path, "w", encoding="utf-8") as f:
        f.write(last_idle_maintain_time.strftime("%Y-%m-%d_%H-%M-%S"))


def assert_ffmpeg():
    try:
        subprocess.run([config.ffmpeg_path, "-version"])
    except FileNotFoundError:
        print("Error: ffmpeg is not installed! Please ensure ffmpeg is in the PATH.")
        sys.exit(1)


# 闲时维护的操作流程
def idle_maintain_process_main():
    global idle_maintaining_in_process
    idle_maintaining_in_process = True
    try:
        threading.Thread(target=ocr_manager.ocr_manager_main, daemon=True).start()
        # 清理过时视频
        ocr_manager.remove_outdated_videofiles()
        # 压缩过期视频
        ocr_manager.compress_outdated_videofiles()
        # 生成随机词表
        wordcloud.generate_all_word_lexicon_by_month()
    except Exception as e:
        print(f"Error on idle maintain: {e}")
    finally:
        idle_maintaining_in_process = False


# 录制完成后索引单个刚录制好的视频文件
def index_video_data(video_saved_dir, vid_file_name):
    print("- Windrecorder -\n---Indexing OCR data\n---")
    full_path = os.path.join(video_saved_dir, vid_file_name)
    if os.path.exists(full_path):
        try:
            print(f"--{full_path} existed. Start ocr processing.")
            ocr_manager.ocr_process_single_video(video_saved_dir, vid_file_name, config.iframe_dir)
        except LockExistsException:
            print(f"--{full_path} ocr is already in process.")


# 录制屏幕
def record_screen(
    output_dir=config.record_videos_dir,
    record_time=config.record_seconds,
):
    """
    用ffmpeg持续录制屏幕,每15分钟保存一个视频文件
    """
    # 构建输出文件名
    now = datetime.datetime.now()
    video_out_name = now.strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
    output_dir_with_date = now.strftime("%Y-%m")  # 将视频存储在日期月份子目录下
    video_saved_dir = os.path.join(output_dir, output_dir_with_date)
    file_utils.ensure_dir(video_saved_dir)
    file_utils.ensure_dir(output_dir)
    out_path = os.path.join(video_saved_dir, video_out_name)

    # 获取屏幕分辨率并根据策略决定缩放
    screen_width, screen_height = utils.get_screen_resolution()
    target_scale_width, target_scale_height = record.get_scale_screen_res_strategy(
        origin_width=screen_width, origin_height=screen_height
    )
    print(f"Origin screen resolution: {screen_width}x{screen_height}, Resized to {target_scale_width}x{target_scale_height}.")

    pix_fmt_args = ["-pix_fmt", "yuv420p"]

    ffmpeg_cmd = [
        config.ffmpeg_path,
        "-f",
        "gdigrab",
        "-video_size",
        f"{screen_width}x{screen_height}",
        "-framerate",
        f"{config.record_framerate}",
        "-i",
        "desktop",
        "-vf",
        f"scale={target_scale_width}:{target_scale_height}",
        # 默认使用编码成 h264 格式
        "-c:v",
        "libx264",
        # 默认码率为 200kbps
        "-b:v",
        f"{config.record_bitrate}k",
        *pix_fmt_args,
        "-t",
        str(record_time),
        out_path,
    ]

    # 执行命令
    try:
        print("record_screen: ffmpeg cmd: ", ffmpeg_cmd)
        # 运行ffmpeg
        subprocess.run(ffmpeg_cmd, check=True)
        print("Windrecorder: Start Recording via FFmpeg")
        return video_saved_dir, video_out_name
    except subprocess.CalledProcessError as ex:
        print(f"Windrecorder: {ex.cmd} failed with return code {ex.returncode}")
        return video_saved_dir, video_out_name


# 持续录制屏幕的主要线程
def continuously_record_screen():
    global last_idle_maintain_time

    while True:
        # 主循环过程
        if monitor_idle_minutes > config.screentime_not_change_to_pause_record:
            print("Windrecorder: Screen content not updated, stop recording.")
            subprocess.run("color 60", shell=True)  # 设定背景色为不活动

            # 算算是否该进入维护了（与上次维护时间相比）
            timegap_between_last_idle_maintain = datetime.datetime.now() - last_idle_maintain_time
            if timegap_between_last_idle_maintain > idle_maintain_time_gap and not idle_maintaining_in_process:  # 超时且无锁情况下
                print(
                    f"Windrecorder: It is separated by {timegap_between_last_idle_maintain} from the last maintenance, enter idle maintenance."
                )
                thread_idle_maintain = threading.Thread(target=idle_maintain_process_main, daemon=True)
                thread_idle_maintain.start()

                # 更新维护时间
                last_idle_maintain_time = datetime.datetime.now()  # 本次闲时维护时间
                with open(config.last_idle_maintain_file_path, "w", encoding="utf-8") as f:
                    f.write(last_idle_maintain_time.strftime("%Y-%m-%d_%H-%M-%S"))

            time.sleep(10)
        else:
            subprocess.run("color 2f", shell=True)  # 设定背景色为活动
            video_saved_dir, video_out_name = record_screen()  # 录制屏幕

            # 自动索引策略
            if config.OCR_index_strategy == 1:
                print(f"Windrecorder: Starting Indexing video data: '{video_out_name}'")
                thread_index_video_data = threading.Thread(
                    target=index_video_data,
                    args=(
                        video_saved_dir,
                        video_out_name,
                    ),
                    daemon=True,
                )
                thread_index_video_data.start()

            time.sleep(2)


# 每隔一段截图对比是否屏幕内容缺少变化
def monitor_compare_screenshot():
    while True:
        if utils.is_screen_locked() or not utils.is_system_awake():
            print("Windrecorder: Screen locked / System not awaked")
        else:
            try:
                global monitor_idle_minutes
                global last_screenshot_array

                while True:
                    similarity = None
                    screenshot = pyautogui.screenshot()
                    screenshot_array = np.array(screenshot)

                    if last_screenshot_array is not None:
                        similarity = ocr_manager.compare_image_similarity_np(last_screenshot_array, screenshot_array)

                        if similarity > 0.9:  # 对比检测阈值
                            monitor_idle_minutes += 0.5
                        else:
                            monitor_idle_minutes = 0

                    last_screenshot_array = screenshot_array.copy()
                    print(f"Windrecorder: monitor_idle_minutes:{monitor_idle_minutes}, similarity:{similarity}")
                    time.sleep(30)
            except Exception as e:
                print("Windrecorder: Error occurred:", str(e))
                if "batchDistance" in str(e):  # 如果是抓不到画面导致出错，可以认为是进入了休眠等情况
                    monitor_idle_minutes += 0.5
                else:
                    monitor_idle_minutes = 0

        time.sleep(5)


# 定时记录前台窗口标题页名
def record_active_window_title():
    while True:
        if not utils.is_screen_locked() or utils.is_system_awake():
            record_wintitle.record_wintitle_now()

        time.sleep(2)


def main():
    subprocess.run("color 60", shell=True)  # 设定背景色为不活动
    assert_ffmpeg()

    while True:
        try:
            recording_lock = FileLock(config.record_lock_path, str(getpid()), timeout_s=None)
        except LockExistsException:
            if record.is_recording():
                print("Windrecorder: Another screen record service is running.")
                sys.exit(1)
            else:
                try:
                    os.remove(config.record_lock_path)
                except FileNotFoundError:
                    pass
                continue

        with recording_lock:
            print(f"Windrecorder: config.OCR_index_strategy: {config.OCR_index_strategy}")

            if config.OCR_index_strategy == 1:
                # 维护之前退出没留下的视频（如果有）
                threading.Thread(target=ocr_manager.ocr_manager_main, daemon=True).start()

            # 屏幕内容多长时间不变则暂停录制
            print(
                f"Windrecorder: config.screentime_not_change_to_pause_record: {config.screentime_not_change_to_pause_record}"
            )
            thread_monitor_compare_screenshot: threading.Thread | None = None
            if config.screentime_not_change_to_pause_record > 0:  # 是否使用屏幕不变检测
                thread_monitor_compare_screenshot = threading.Thread(target=monitor_compare_screenshot, daemon=True)
                thread_monitor_compare_screenshot.start()

            # 录屏的线程
            thread_continuously_record_screen = threading.Thread(target=continuously_record_screen, daemon=True)
            thread_continuously_record_screen.start()

            # 记录前台窗体标题的进程：
            thread_record_active_window_title = threading.Thread(target=record_active_window_title, daemon=True)
            thread_record_active_window_title.start()

            while True:
                # 如果屏幕重复画面检测线程意外出错，重启它
                if thread_monitor_compare_screenshot is not None and not thread_monitor_compare_screenshot.is_alive():
                    thread_monitor_compare_screenshot = threading.Thread(target=monitor_compare_screenshot, daemon=True)
                    thread_monitor_compare_screenshot.start()

                # 如果屏幕录制线程意外出错，重启它
                if not thread_continuously_record_screen.is_alive():
                    thread_continuously_record_screen = threading.Thread(target=continuously_record_screen, daemon=True)
                    thread_continuously_record_screen.start()

                # 如果记录窗体标题进程出错，重启它
                if not thread_record_active_window_title.is_alive():
                    thread_record_active_window_title = threading.Thread(target=record_active_window_title, daemon=True)
                    thread_record_active_window_title.start()

                time.sleep(30)


if __name__ == "__main__":
    main()
