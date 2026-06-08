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
