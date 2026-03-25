"""
CNKI_Bug_dev - 中国知网论文标题爬虫
版本: 0.0.6
作者: Kaffu_Alcaid
打包说明: pyinstaller --onefile --console --name CNKIBug CNKIBug_dev0.0.6.py
"""

import sys
import os
import shutil
import subprocess
import winreg


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


try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    import openpyxl
except ImportError as _err:
    if sys.platform == "win32":
        _popup_error([
            "==============================================",
            " [致命错误] 程序核心组件加载失败！",
            "----------------------------------------------",
            f" 缺失模块: {_err}",
            "",
            " 可能原因：您运行的不是完整的 exe 文件，",
            " 或 exe 文件已损坏。",
            "",
            " 解决方法：",
            "   请重新下载完整的 CNKIBug.exe 文件，",
            "   不要解压、不要移动内部文件，直接双击运行。",
            "==============================================",
        ])
    else:
        print(f"[FATAL] 缺少依赖: {_err}")
        print("请运行: pip install playwright openpyxl && playwright install chromium")
    sys.exit(1)

import time
import random

_EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
]

def _edge_installed() -> bool:
    if any(os.path.isfile(p) for p in _EDGE_PATHS):
        return True
    return shutil.which("msedge") is not None


def get_real_desktop_path() -> str:
    if sys.platform != "win32":
        return os.path.join(os.path.expanduser("~"), "Desktop")
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        val, _ = winreg.QueryValueEx(key, "Desktop")
        winreg.CloseKey(key)
        return os.path.expandvars(val)
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")


def check_env():
    if sys.platform != "win32":
        playwright_path = os.path.join(
            os.path.expanduser("~"), "AppData", "Local", "ms-playwright"
        )
        if not os.path.exists(playwright_path):
            print("\n[环境缺失] 请先在终端运行: playwright install chromium\n")
            sys.exit(0)
        return

    if not _edge_installed():
        _popup_error([
            "==============================================",
            " [环境缺失] 未检测到 Microsoft Edge 浏览器！",
            "----------------------------------------------",
            " 本程序需要使用 Microsoft Edge 来抓取网页数据。",
            " Windows 10/11 通常已预装，若您已卸载请重新安装。",
            "",
            " 请用浏览器打开以下地址，下载并安装 Edge：",
            "",
            "   https://www.microsoft.com/zh-cn/edge/download",
            "",
            " 安装完成后，关闭此窗口，重新双击程序即可！",
            "==============================================",
        ])
        sys.exit(0)


def _check_captcha(page) -> bool:
    captcha_selectors = [
        "div.captcha",
        "div#captcha",
        "iframe[src*='captcha']",
        "div.slide-verify",
        "div.passcode-area",
    ]
    for sel in captcha_selectors:
        if page.query_selector(sel):
            print("\n" + "!" * 50)
            print("  [!] 检测到人机验证！请切换到浏览器窗口处理：")
            print("  · 如果是【滑块验证】：按住滑块向右拖动到底")
            print("  · 如果是【图片验证码】：按提示点击或输入字符")
            print("  · 完成验证后，回到此窗口按 [回车键] 继续...")
            print("!" * 50)
            input()
            return True
    return False


def scrape_cnki(keyword: str, max_pages: int = 3):
    results = []

    with sync_playwright() as p:
        browser = None

        try:
            browser = p.chromium.launch(channel="msedge", headless=False)
            print("[*] 已启动 Microsoft Edge 浏览器")
        except Exception as _e1:
            print(f"[!] Edge 启动失败 ({_e1})，尝试备用 Chromium...")
            try:
                browser = p.chromium.launch(headless=False)
                print("[*] 已启动备用 Chromium 浏览器")
            except Exception as _e2:
                if sys.platform == "win32":
                    _popup_error([
                        "==============================================",
                        " [错误] 浏览器启动失败！",
                        "----------------------------------------------",
                        " 程序找到了 Edge，但无法正常启动它。",
                        "",
                        " 可能原因：",
                        "   · Edge 浏览器文件损坏",
                        "   · 系统权限不足",
                        "   · 安全软件阻止了浏览器启动",
                        "",
                        " 建议：",
                        "   1. 重新安装 Microsoft Edge",
                        "      https://www.microsoft.com/zh-cn/edge/download",
                        "   2. 以管理员身份运行本程序",
                        "   3. 暂时关闭杀毒软件后重试",
                        "==============================================",
                    ])
                else:
                    print(f"[FATAL] 浏览器启动失败: {_e2}")
                raise RuntimeError(f"浏览器启动彻底失败: {_e2}")

        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            print(f"[*] 目标关键词：{keyword}")
            page.goto("https://kns.cnki.net/kns8s/")
            time.sleep(random.uniform(2, 4))

            _check_captcha(page)

            page.fill("input.search-input", keyword)
            time.sleep(random.uniform(0.5, 1.5))
            page.click("input.search-btn")
            time.sleep(random.uniform(1, 2))
            _check_captcha(page)

            for current_page in range(1, max_pages + 1):
                print(f"[*] 读取第 {current_page} 页...")
                page.wait_for_selector(
                    "table.result-table-list tbody tr", timeout=15000
                )
                time.sleep(random.uniform(2, 5))

                _check_captcha(page)

                rows = page.query_selector_all("table.result-table-list tbody tr")
                for row in rows:
                    title_el = row.query_selector("td.name a")
                    if title_el:
                        title = title_el.inner_text().strip()
                        results.append([title])
                        print(f"  -> 抓取到: {title}")

                if current_page < max_pages:
                    next_btn = page.query_selector("a#PageNext")
                    if next_btn:
                        next_btn.click()
                        time.sleep(random.uniform(4, 8))
                        _check_captcha(page)
                    else:
                        print("[!] 没找到下一页按钮，可能已到最后一页。")
                        break


        except PlaywrightTimeoutError as e:
            print(f"[x] 页面等待超时，可能网络较慢或知网结构变化: {e}")
        except RuntimeError as e:
            print(f"[x] 运行时错误（可能遇到验证码或页面结构变化）: {e}")
        finally:
            if browser:
                browser.close()

    if not results:
        print("[!] 未抓取到任何数据，不生成文件。")
        return

    try:
        real_desktop = get_real_desktop_path()
        os.makedirs(real_desktop, exist_ok=True)
        filename = os.path.join(real_desktop, f"cnki_titles_{keyword}.xlsx")
    except OSError:
        filename = os.path.join(os.getcwd(), f"cnki_titles_{keyword}.xlsx")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "论文标题"
    ws.append(["论文标题"])
    for row in results:
        ws.append(row)

    try:
        wb.save(filename)
    except PermissionError:
        print(f"\n[x] 文件保存失败：没有写入权限！")
        print(f"    目标路径：{filename}")
        print(f"    请确认桌面文件夹未被锁定，或关闭已打开的同名 Excel 文件。")
        return
    except OSError as save_err:
        print(f"\n[x] 文件保存失败：{save_err}")
        print(f"    可能原因：磁盘空间不足，或路径不可写。")
        print(f"    尝试保存到程序目录...")
        fallback = os.path.join(os.getcwd(), f"cnki_titles_{keyword}.xlsx")
        try:
            wb.save(fallback)
            filename = fallback
        except OSError as fb_err:
            print(f"[x] 备用路径也保存失败：{fb_err}")
            return

    full_path = os.path.abspath(filename)
    print("\n" + "=" * 50)
    print(f"[*] 共抓取 {len(results)} 条数据。")
    print(f"[*] 文件已保存至：")
    print(f"    >>> {full_path} <<<")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            os.system("cls")

        print("=" * 50)
        print("  CNKI_Bug_dev  |  copyright by Kaffu_Alcaid")
        print("  Version 0.0.6")
        print("=" * 50)
        print("  本软件用于抓取中国知网的论文标题\n")
        print("按 Ctrl+C 可随时退出程序")

        check_env()

        search_word = input("\n请输入你要搜索的关键词: ").strip()
        if not search_word:
            print("[!] 关键词不能为空，程序退出。")
            sys.exit(0)

        user_input_pages = input("请输入想抓取的总页数（纯数字，值不要太大）: ").strip()
        target_pages = int(user_input_pages)
        if target_pages <= 0:
            raise ValueError("页数必须大于 0")

        scrape_cnki(search_word, max_pages=target_pages)

    except ValueError:
        print("\n" + "!" * 40)
        print("  错误：页数请输入【纯数字】，例如 3 或 10")
        print("!" * 40)
    except KeyboardInterrupt:
        print("\n[*] 用户中断，程序退出。")
    except RuntimeError as e:
        print(f"\n[!] {e}")
    except Exception as ex:
        print("\n" + "!" * 40)
        print(f"  程序遇到未知错误: {ex}")
        print("!" * 40)
    finally:
        input("\n按 [回车键 Enter] 退出程序...")
