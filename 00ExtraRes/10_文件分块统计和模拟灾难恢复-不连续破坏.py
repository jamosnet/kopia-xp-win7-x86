import os
import shutil
import hashlib
import subprocess
import random
import glob
import json, time

# ================= 配置区域 =================
BASE_DIR = r"C:\KopiaUI-0.22.3-win"
KOPIA_EXE = os.path.join(BASE_DIR, r"resources\server\kopia.exe")
REPO_DIR = r"C:\kopia_Backup_12400f"
SOURCE_DIR = r"C:\kopia_test_source"
RESTORE_DIR = r"C:\kopia_test_restore"
CACHE_DIR = r"C:\KopiaCache"
KOPIA_PASSWORD = "test_password_123"

MD_RESULT_FILE = "kopia_test_result_11.md"

FILE_COUNT = 50
FILE_SIZE_KB = 500

# ================= 环境变量设置 =================
os.environ["KOPIA_PASSWORD"] = KOPIA_PASSWORD
os.environ["KOPIA_EXPERIMENTAL"] = "true"
os.environ["KOPIA_CHECK_FOR_UPDATES"] = "false"
os.environ["KOPIA_CACHE_DIRECTORY"] = CACHE_DIR


def run_cmd(cmd, check=True, show_log=False):
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8")
    if show_log and result.stderr:
        print("\n[👇 Kopia 原始底层日志 👇]")
        print(result.stderr.strip())
        print("[👆 =================== 👆]\n")

    if check and result.returncode != 0:
        print(f"命令执行出错: {' '.join(cmd)}\n详细错误: {result.stderr}")
        exit(1)
    return result


def setup_repo():
    if os.path.exists(REPO_DIR): shutil.rmtree(REPO_DIR)
    if os.path.exists(CACHE_DIR): shutil.rmtree(CACHE_DIR)

    run_cmd([
        KOPIA_EXE, "repository", "create", "filesystem",
        f"--path={REPO_DIR}",
        "--ecc=REED-SOLOMON-CRC32",
        "--ecc-overhead-percent=5"
    ])
    run_cmd([KOPIA_EXE, "policy", "set", "--global", "--compression=zstd"])


def generate_test_files():
    if os.path.exists(SOURCE_DIR): shutil.rmtree(SOURCE_DIR)
    os.makedirs(SOURCE_DIR)

    print(f"[*] 开始生成 {FILE_COUNT} 个测试文件 ({FILE_SIZE_KB}KB/个)...")
    file_size_bytes = FILE_SIZE_KB * 1024

    for _ in range(FILE_COUNT):
        data = os.urandom(file_size_bytes)
        file_hash = hashlib.sha256(data).hexdigest()
        file_path = os.path.join(SOURCE_DIR, f"{file_hash}.bin")
        with open(file_path, "wb") as f:
            f.write(data)


def backup_and_get_snapshot():
    print("[*] 正在执行 Kopia 备份...")
    run_cmd([KOPIA_EXE, "snapshot", "create", SOURCE_DIR])
    res = run_cmd([KOPIA_EXE, "snapshot", "list", SOURCE_DIR, "--json"])
    try:
        snapshot_data = json.loads(res.stdout)
        return snapshot_data[0]['id']
    except Exception as e:
        print(f"[-] JSON 解析快照失败: {e}")
        exit(1)


def analyze_and_corrupt_pack(corruption_level, is_continuous=True, max_chunk_size=1024):
    """
    破坏 Pack 数据块的函数
    :param corruption_level: 破坏比例 (float) 或 "delete"
    :param is_continuous: True 表示集中破坏中间的一段；False 表示随机分散在文件中破坏
    :param max_chunk_size: 当 is_continuous=False 时有效，控制单次破坏的最大连续字节数
    """
    pack_files = glob.glob(os.path.join(REPO_DIR, "p", "**", "*.f"), recursive=True)
    pack_count = len(pack_files)
    if pack_count == 0: return 0

    target_pack = random.choice(pack_files)
    file_size = os.path.getsize(target_pack)
    print(f"[*] 仓库共有 {pack_count} 个块。选中目标: {os.path.basename(target_pack)} ({file_size / 1024 / 1024:.2f}MB)")

    if corruption_level == "delete":
        os.remove(target_pack)
        print("[!] 已物理删除该数据块。")
        return pack_count

    safe_margin = 1024 * 1024
    if file_size <= safe_margin * 2:
        safe_margin = int(file_size * 0.1)

    max_corruptible = file_size - (safe_margin * 2)
    corrupt_bytes_count = int(file_size * corruption_level)
    corrupt_bytes_count = max(1, min(corrupt_bytes_count, max_corruptible))

    with open(target_pack, "rb") as f:
        data = bytearray(f.read())

    if is_continuous:
        # 【模式 1】连续破坏：在文件中间挖掉一大块
        print(f"[!] 准备【连续破坏】该块 {corrupt_bytes_count} 字节 ({corruption_level * 100}%)...")
        start_idx = (file_size // 2) - (corrupt_bytes_count // 2)
        data[start_idx: start_idx + corrupt_bytes_count] = os.urandom(corrupt_bytes_count)
        print(f"[+] 破坏完成 (影响区间: {start_idx} -> {start_idx + corrupt_bytes_count})。")
    else:
        # 【模式 2】分散破坏：随机打点，直到达到指定的破坏总字节数
        print(f"[!] 准备【随机分散破坏】该块共 {corrupt_bytes_count} 字节 ({corruption_level * 100}%)，单处最大 {max_chunk_size} 字节...")
        remaining_to_corrupt = corrupt_bytes_count
        corrupt_spots = 0

        while remaining_to_corrupt > 0:
            # 决定这一次随机破坏多少字节（1 到 max_chunk_size 之间）
            current_chunk = min(remaining_to_corrupt, random.randint(1, max_chunk_size))

            # 在安全边距内随机找一个起点
            start_idx = random.randint(safe_margin, file_size - safe_margin - current_chunk)

            # 注入随机垃圾数据
            data[start_idx: start_idx + current_chunk] = os.urandom(current_chunk)

            remaining_to_corrupt -= current_chunk
            corrupt_spots += 1

        print(f"[+] 破坏完成 (共在 {corrupt_spots} 个随机位置造成了坏道)。")

    with open(target_pack, "wb") as f:
        f.write(data)

    return pack_count


def force_full_ecc_repair():
    print("[*] 正在下达最高指令：执行全量底层字节体检与修复 (kopia content verify --verify-full)...")
    run_cmd([KOPIA_EXE, "content", "verify", "--full"], check=False, show_log=True)
    print("[*] 尝试执行仓库底层修复指令 (kopia maintenance run)...")
    run_cmd([KOPIA_EXE, "maintenance", "run", "--full", "--force"], check=False, show_log=True)


def restore_and_verify(snapshot_id):
    if os.path.exists(RESTORE_DIR): shutil.rmtree(RESTORE_DIR)
    os.makedirs(RESTORE_DIR)
    if os.path.exists(CACHE_DIR): shutil.rmtree(CACHE_DIR)

    print("[*] 正在执行容错恢复操作 (--ignore-errors)...")
    res = run_cmd([KOPIA_EXE, "snapshot", "restore", snapshot_id, RESTORE_DIR, "--ignore-errors"], check=False, show_log=True)

    restored_files = os.listdir(RESTORE_DIR)
    print(f"[*] 写入硬盘的文件数: {len(restored_files)} 个")

    success_count = 0
    for filename in restored_files:
        expected_hash = filename.replace(".bin", "")
        with open(os.path.join(RESTORE_DIR, filename), "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == expected_hash:
                success_count += 1

    success_rate = (success_count / FILE_COUNT) * 100
    print(f"==================================================")
    print(f" 恢复统计: 期望 {FILE_COUNT} 个, 校验成功 {success_count} 个")
    print(f" 成功率: {success_rate:.1f}%")
    print(f"==================================================\n")
    return success_count, success_rate


def generate_markdown_report(results):
    md_content = "# Kopia ECC 灾备测试报告 (方案 10 - 终极修复尝试与日志剖析)\n\n"
    md_content += f"- **测试文件**: {FILE_COUNT} 个, 单个 {FILE_SIZE_KB} KB\n"
    md_content += f"- **ECC冗余**: REED-SOLOMON-CRC32 (设定为 5% Overhead)\n\n"

    md_content += "| 破坏级别 / 模式 | 仓库P块数 | 期望文件 | 成功校验 | 恢复成功率 | 测试说明 |\n"
    md_content += "| :--- | :---: | :---: | :---: | :---: | :--- |\n"

    for row in results:
        md_content += f"| {row['level']} | {row['p_count']} | {FILE_COUNT} | {row['success']} | {row['rate']:.1f}% | - |\n"

    with open(MD_RESULT_FILE, "w", encoding="utf-8") as f:
        f.write(md_content)


def run_ecc_test_suite():
    # ================= 核心测试参数配置 =================
    # 格式: (破坏比例, 是否连续破坏, 单次最大破坏字节)
    # 若破坏比例为 "delete" 则触发物理删除，后面两个参数无效
    test_scenarios = [
        (0.0003, True, 0),  # 场景 1: 0.03% 连续破坏
        (0.0003, False, 16),  # 场景 2: 0.03% 分散破坏，模拟随机的小型坏道（单处最大 16 字节）
        (0.0003, False, 256),  # 场景 3: 0.03% 分散破坏，模拟随机的小型坏道（单处最大 256 字节）
        (0.001, False, 256),  # 场景 4: 0.1%  分散破坏，模拟稍微大一点的面状坏道（单处最大 256 字节）
        # ("delete", True, 0)       # 场景 4: 直接物理删除整个包 (如需测试取消注释)
    ]

    test_results = []

    for level, is_cont, max_chunk in test_scenarios:
        # 生成直观的测试场景名称
        if level == "delete":
            level_str = "物理删除数据块"
        else:
            mode_str = "中段连续坏道" if is_cont else f"分散坏道(单点最大{max_chunk}B)"
            level_str = f"{(level * 100):.3f}% {mode_str}"

        print(f"\n>>>>>>>>>> 开始测试: {level_str} <<<<<<<<<<")

        setup_repo()
        generate_test_files()
        snapshot_id = backup_and_get_snapshot()

        p_count = analyze_and_corrupt_pack(level, is_continuous=is_cont, max_chunk_size=max_chunk)
        success_count, rate = restore_and_verify(snapshot_id)

        test_results.append({
            "level": level_str,
            "p_count": p_count,
            "success": success_count,
            "rate": rate
        })

    generate_markdown_report(test_results)


if __name__ == "__main__":
    run_ecc_test_suite()