"""错误弹窗 —— 仅依赖标准库，保证在三方依赖缺失时仍可被 import 并弹窗。

说明：本模块不得引入任何三方依赖，否则它本身就无法在依赖缺失场景下加载，
依赖守卫（见 run.py）就失去意义。subprocess.CREATE_NEW_CONSOLE 是
Windows 专属属性，仅在函数体内被引用，因此本模块在非 Windows 平台也能 import，
只是不能调用 _popup_error（而它本来也只在 win32 分支下被调用）。
"""

import subprocess


def _popup_error(lines: list[str]):
    echo_cmds = []
    for ln in lines:
        if ln.strip():
            echo_cmds.append(f"echo {ln}")
        else:
            echo_cmds.append("echo.")

    inner = " & ".join(echo_cmds) + " & echo. & pause "
    subprocess.Popen(
        ["cmd.exe", "/k", f"color 4E & {inner}"],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
