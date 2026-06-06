"""
ManageCamera 管理员注册/清理工具

功能:
  1. 删除系统中的 Kwai Virtual Camera 和其他残留虚拟摄像头
  2. 将 ManageCamera 注册到 HKLM（机器全局可见）
  3. OBS/Chrome/Zoom 等应用均可识别

用法:
  以管理员身份运行终端，然后:
    python virtual_cam/register_admin.py

注意: 必须以管理员身份运行才能写入/删除 HKLM。
"""

import sys
import os
import ctypes
import subprocess
import winreg
from pathlib import Path


def is_admin():
    """检查是否以管理员身份运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


# ── 需要清理的第三方虚拟摄像头 ──
STALE_CLSIDS = {
    # Kwai Virtual Camera (快手)
    "{FDE06CAC-5D0D-24BC-36E1-BEF87DEFF885}": "Kwai Virtual Camera",
    # WebcastMate VirtualCamera
    "{0C36C4D6-8672-4C6E-A446-CDEC9D0CB1A7}": "WebcastMate VirtualCamera",
}

# CLSID 类别 GUID
CLSID_VIDEO_INPUT_DEVICE = "{860BB310-5D01-11D0-BD3B-00A0C911CE86}"
CLSID_LEGACY_AM_FILTER = "{083863F1-70DE-11D0-BD40-00A0C911CE86}"
CLSID_KSCATEGORY_CAPTURE = "{65E8773D-8F56-11D0-A3B9-00A0C9223196}"

# Merit — MERIT_PREFERRED 排在其他设备之前
VCAM_MERIT = 0x00800000

# ManageCamera CLSID
OUR_CLSID = "{5C2CD55C-92AD-4999-8666-912BD3E70020}"

# 旧的有 bug 的 CLSID (13 字符最后一组 — 必须清理!)
BROKEN_OUR_CLSID = "{5C2CD55C-92AD-4999-8666-912BD3E700020}"


def _get_root_name(root):
    """将 winreg.HKEY_* 常量转换为 reg 命令可用的根键名"""
    _map = {
        winreg.HKEY_LOCAL_MACHINE: "HKLM",
        winreg.HKEY_CURRENT_USER: "HKCU",
        winreg.HKEY_CLASSES_ROOT: "HKCR",
    }
    return _map.get(root, "HKLM")


def del_reg_tree_safe(root, subkey):
    """安全删除注册表树。使用 reg delete 命令（兼容所有 Python 版本）。"""
    root_name = _get_root_name(root)
    full_path = f"{root_name}\\{subkey}"
    try:
        result = subprocess.run(
            ["reg", "delete", full_path, "/f"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def remove_stale_camera(clsid, name, root=winreg.HKEY_LOCAL_MACHINE):
    """删除指定 CLSID 的第三方虚拟摄像头注册"""
    clsid_path = f"SOFTWARE\\Classes\\CLSID\\{clsid}"
    viddev_path = f"SOFTWARE\\Classes\\CLSID\\{CLSID_VIDEO_INPUT_DEVICE}\\Instance\\{clsid}"
    legacy_path = f"SOFTWARE\\Classes\\CLSID\\{CLSID_LEGACY_AM_FILTER}\\Instance\\{clsid}"
    kscap_path = f"SOFTWARE\\Classes\\CLSID\\{CLSID_KSCATEGORY_CAPTURE}\\Instance\\{clsid}"

    removed = []

    # 1) VideoInputDevice 实例
    if del_reg_tree_safe(root, viddev_path):
        removed.append("VideoInputDevice")

    # 2) Legacy AM Filter 实例
    if del_reg_tree_safe(root, legacy_path):
        removed.append("LegacyAMFilter")

    # 3) KSCATEGORY_CAPTURE 实例
    if del_reg_tree_safe(root, kscap_path):
        removed.append("KSCATEGORY_CAPTURE")

    # 4) CLSID 本身
    if del_reg_tree_safe(root, clsid_path):
        removed.append("CLSID")

    # 5) 也检查 HKCU
    hkcu_clsid = f"Software\\Classes\\CLSID\\{clsid}"
    hkcu_viddev = f"Software\\Classes\\CLSID\\{CLSID_VIDEO_INPUT_DEVICE}\\Instance\\{clsid}"
    hkcu_kscap = f"Software\\Classes\\CLSID\\{CLSID_KSCATEGORY_CAPTURE}\\Instance\\{clsid}"
    if del_reg_tree_safe(winreg.HKEY_CURRENT_USER, hkcu_clsid):
        removed.append("HKCU_CLSID")
    if del_reg_tree_safe(winreg.HKEY_CURRENT_USER, hkcu_viddev):
        removed.append("HKCU_VidDev")
    if del_reg_tree_safe(winreg.HKEY_CURRENT_USER, hkcu_kscap):
        removed.append("HKCU_KSCAP")

    return removed


def scan_and_clean():
    """扫描并清理所有已知的第三方虚拟摄像头"""
    print("===== 扫描第三方虚拟摄像头 =====")

    for clsid, name in STALE_CLSIDS.items():
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                f"SOFTWARE\\Classes\\CLSID\\{clsid}"
            )
            dv, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            print(f"  发现: {name} (CLSID={clsid}) 注册在 HKLM")
            print(f"         DLL: {_get_dll_path_for_clsid(clsid)}")
        except FileNotFoundError:
            continue
        except Exception:
            continue

    print()


def _get_dll_path_for_clsid(clsid):
    """获取 CLSID 的 InprocServer32 路径"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            f"SOFTWARE\\Classes\\CLSID\\{clsid}\\InprocServer32"
        )
        path, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        return path
    except Exception:
        return "(无法读取)"


def clean_all_stale():
    """清理所有已知的第三方虚拟摄像头"""
    print("===== 清理第三方虚拟摄像头 =====")
    total = 0

    # A) 清理已知的第三方残留 CLSID
    for clsid, name in STALE_CLSIDS.items():
        removed = remove_stale_camera(clsid, name)
        if removed:
            print(f"  [{name}] 已删除: {', '.join(removed)}")
            total += len(removed)
        else:
            print(f"  [{name}] 未找到残留注册（已清理干净）")

    # A2) 清理我们自己旧的有 bug 的 CLSID
    removed_old = remove_stale_camera(BROKEN_OUR_CLSID, "OLD Virtual Camera (buggy CLSID)")
    if removed_old:
        print(f"  [旧 Bug CLSID] 已清理: {', '.join(removed_old)}")
        total += len(removed_old)
    else:
        print(f"  [旧 Bug CLSID] 未找到（已清理干净）")

    # B) 清理可能泄漏的字面量 %%s 条目
    stray_paths = [
        (winreg.HKEY_CURRENT_USER, f"Software\\Classes\\CLSID\\{CLSID_VIDEO_INPUT_DEVICE}\\Instance\\%s"),
        (winreg.HKEY_CURRENT_USER, f"Software\\Classes\\CLSID\\{CLSID_LEGACY_AM_FILTER}\\Instance\\%s"),
        (winreg.HKEY_CURRENT_USER, f"Software\\Classes\\CLSID\\{CLSID_KSCATEGORY_CAPTURE}\\Instance\\%s"),
    ]
    for root, path in stray_paths:
        try:
            key = winreg.OpenKey(root, path)
            winreg.CloseKey(key)
            if del_reg_tree_safe(root, path):
                print(f"  [泄漏条目] 已删除: {path}")
                total += 1
        except FileNotFoundError:
            pass
        except Exception:
            pass

    print(f"  共清理 {total} 项")
    print()


def register_our_camera():
    """将 ManageCamera 注册到 HKLM"""
    print("===== 注册 ManageCamera 到 HKLM =====")

    vcam_dir = Path(__file__).parent / "vcam_filter"
    dll_path = (vcam_dir / "vcam_filter.dll").resolve()

    if not dll_path.exists():
        print(f"  错误: DLL 不存在: {dll_path}")
        return False

    print(f"  DLL: {dll_path}")

    # 先卸载旧注册
    try:
        dll = ctypes.WinDLL(str(dll_path))
        dll.DllUnregisterServer()
        print("  已清除旧注册")
    except Exception as e:
        print(f"  清除旧注册跳过: {e}")

    # 重新注册 (DllRegisterServer 会在管理员模式下写入 HKLM + HKCU)
    try:
        dll = ctypes.WinDLL(str(dll_path))
        hr = dll.DllRegisterServer()
        if hr == 0:
            print(f"  注册成功!")
        else:
            print(f"  注册返回非零: HRESULT=0x{hr:08X}")
            return False
    except Exception as e:
        print(f"  注册失败: {e}")
        return False

    # 验证
    print()
    print("  验证注册表...")
    ok = verify_registration()
    return ok


def verify_registration():
    """验证 HKLM + HKCU 注册是否正确"""
    checks = [
        ("HKLM CLSID 默认值", winreg.HKEY_LOCAL_MACHINE, f"SOFTWARE\\Classes\\CLSID\\{OUR_CLSID}", ""),
        ("HKLM VideoInputDevice", winreg.HKEY_LOCAL_MACHINE, f"SOFTWARE\\Classes\\CLSID\\{CLSID_VIDEO_INPUT_DEVICE}\\Instance\\{OUR_CLSID}", "FriendlyName"),
        ("HKLM KSCATEGORY", winreg.HKEY_LOCAL_MACHINE, f"SOFTWARE\\Classes\\CLSID\\{CLSID_KSCATEGORY_CAPTURE}\\Instance\\{OUR_CLSID}", "FriendlyName"),
        ("HKCU CLSID 默认值", winreg.HKEY_CURRENT_USER, f"Software\\Classes\\CLSID\\{OUR_CLSID}", ""),
        ("HKCU VideoInputDevice", winreg.HKEY_CURRENT_USER, f"Software\\Classes\\CLSID\\{CLSID_VIDEO_INPUT_DEVICE}\\Instance\\{OUR_CLSID}", "FriendlyName"),
        ("HKCU KSCATEGORY", winreg.HKEY_CURRENT_USER, f"Software\\Classes\\CLSID\\{CLSID_KSCATEGORY_CAPTURE}\\Instance\\{OUR_CLSID}", "FriendlyName"),
    ]

    all_ok = True
    for desc, root, path, value_name in checks:
        try:
            key = winreg.OpenKey(root, path)
            if value_name:
                val, _ = winreg.QueryValueEx(key, value_name)
            else:
                val, _ = winreg.QueryValueEx(key, "")
            print(f"    [OK] {desc}: {val}")
            winreg.CloseKey(key)
        except FileNotFoundError:
            print(f"    [MISSING] {desc}")
            all_ok = False
        except Exception as e:
            print(f"    [ERR] {desc}: {e}")
            all_ok = False

    return all_ok


def main():
    interactive = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else True

    if interactive:
        print("=" * 60)
        print("  ManageCamera 管理员注册 / 清理工具")
        print("=" * 60)
        print()

    if not is_admin():
        print(">>> 错误: 此操作需要管理员权限 <<<")
        print()
        print("请按以下步骤操作:")
        print("  1. 关闭当前终端")
        print("  2. 右键点击终端图标 → 以管理员身份运行")
        print("  3. 运行以下命令:")
        print(f"     python {__file__}")
        print()
        if interactive:
            input("按 Enter 退出...")
        return False

    print("已获取管理员权限")
    print()

    # 步骤 1: 扫描
    scan_and_clean()

    # 步骤 2: 清理第三方
    clean_all_stale()

    # 步骤 3: 注册我们的
    register_our_camera()

    print()
    print("=" * 60)
    print("  完成!")
    print()
    print("现在可以在以下应用中看到 ManageCamera:")
    print("  - OBS Studio (添加源 → 视频捕获设备)")
    print("  - Chrome/Edge 浏览器 (摄像头权限请求)")
    print("  - Zoom (设置 → 视频)")
    print("=" * 60)

    if interactive:
        input("按 Enter 退出...")
    else:
        import time
        print("(3 秒后自动关闭...)")
        time.sleep(3)
    return True


if __name__ == "__main__":
    main()
