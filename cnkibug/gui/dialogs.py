from __future__ import annotations

import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.dialogs import Messagebox, QueryDialog
from ttkbootstrap.icons import Icon


YES = "yes"
NO = "no"
OK = "ok"
CANCEL = "cancel"


def _show(title, message, buttons, *, parent=None, default=None, icon="info"):
    icons = {"info": Icon.info, "warning": Icon.warning, "error": Icon.error, "question": Icon.question}
    previous_grab = parent.grab_current() if parent is not None else None
    try:
        return Messagebox.show_question(
            title=title, message=message, parent=parent, buttons=buttons,
            default=default, icon=icons[icon], alert=False, localize=False,
        )
    finally:
        _restore_grab(previous_grab)


def _restore_grab(window):
    if window is not None:
        try:
            if window.winfo_exists():
                window.grab_set()
        except tk.TclError:
            pass


def showinfo(title, message, *, parent=None):
    _show(title, message, ["确定:primary"], parent=parent, default="确定")
    return OK


def showwarning(title, message, *, parent=None):
    _show(title, message, ["确定:primary"], parent=parent, default="确定", icon="warning")
    return OK


def showerror(title, message, *, parent=None):
    _show(title, message, ["确定:primary"], parent=parent, default="确定", icon="error")
    return OK


def askyesno(title, message, *, parent=None, default=YES, icon="question"):
    return _show(
        title, message, ["否:secondary", "是:primary"], parent=parent,
        default="否" if default == NO else "是", icon=icon,
    ) == "是"


def askyesnocancel(title, message, *, parent=None, default=YES, icon="question"):
    result = _show(
        title, message, ["取消:secondary", "否:secondary", "是:primary"], parent=parent,
        default={YES: "是", NO: "否", CANCEL: "取消"}[default], icon=icon,
    )
    return None if result in {None, "取消"} else result == "是"


def askokcancel(title, message, *, parent=None, default=OK, confirm="确定", icon="question"):
    return _show(
        title, message, ["取消:secondary", f"{confirm}:primary"], parent=parent,
        default="取消" if default == CANCEL else confirm, icon=icon,
    ) == confirm


class _StringDialog(QueryDialog):
    def create_buttonbox(self, master):
        buttons = ttk.Frame(master, padding=(20, 0, 20, 16))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="确定", command=self.on_submit, bootstyle="primary").pack(side=tk.RIGHT)
        ttk.Button(buttons, text="取消", command=self.on_cancel, bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=(0, 8))


def askstring(title, prompt, *, initialvalue="", parent=None):
    previous_grab = parent.grab_current() if parent is not None else None
    try:
        dialog = _StringDialog(prompt, title, initialvalue, parent=parent)
        dialog.show()
        return dialog.result
    finally:
        _restore_grab(previous_grab)
