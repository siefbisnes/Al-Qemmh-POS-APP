"""
Al-Qemma launcher GUI for Windows.

Shows a simple connecting window with progress steps, a log button,
and then minimizes to the taskbar once the server is ready.
"""
import os
import sys
import threading
import time
import webbrowser
import subprocess
import logging

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    tk = None


LOG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "launcher.log"))


class LauncherWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Al-Qemma Launcher")
        self.root.resizable(False, False)
        self.root.geometry("460x320")

        self.status_var = tk.StringVar(value="Connecting...")
        self.steps = []

        self._build_ui()
        self._configure_logging()
        self.server_thread = None

    def _build_ui(self):
        self.root.configure(bg="#101216")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Dark.TFrame", background="#101216")
        style.configure("Dark.TLabel", background="#101216", foreground="#E9ECEF")
        style.configure("Dark.Small.TLabel", background="#101216", foreground="#9AA3B0", font=(None, 10))
        style.configure("Dark.TButton", background="#161B22", foreground="#E9ECEF", borderwidth=0, focusthickness=3, focuscolor="#4C9AFF")
        style.map("Dark.TButton", background=[("active", "#1F252F")], foreground=[("active", "#FFFFFF")])
        style.configure("Accent.Horizontal.TProgressbar", troughcolor="#161B22", background="#4C9AFF", bordercolor="#101216", lightcolor="#63B3FF", darkcolor="#1A73E8")

        frame = ttk.Frame(self.root, style="Dark.TFrame", padding=18)
        frame.pack(fill="both", expand=True)

        title = ttk.Label(frame, text="Connecting", style="Dark.TLabel", font=(None, 22, "bold"))
        title.pack(anchor="w")

        self.progress = ttk.Progressbar(frame, style="Accent.Horizontal.TProgressbar", mode="indeterminate", maximum=100)
        self.progress.pack(fill="x", pady=(18, 12))
        self.progress.start(12)

        self.status_label = ttk.Label(frame, textvariable=self.status_var, style="Dark.Small.TLabel")
        self.status_label.pack(anchor="w")

        self.toggle_button = ttk.Button(frame, text="LOG", style="Dark.TButton", command=self.toggle_log)
        self.toggle_button.pack(anchor="e", pady=(14, 0))

        self.log_container = ttk.Frame(frame, style="Dark.TFrame")
        self.log_container.pack(fill="both", expand=False, pady=(10, 0))
        self.log_container.pack_forget()

        self.log_box = tk.Text(self.log_container, wrap="word", height=10, state="disabled", bg="#090B10", fg="#D8DEE9", insertbackground="#FFFFFF", bd=0, highlightthickness=0, padx=8, pady=8, font=("Consolas", 10))
        self.log_box.pack(fill="both", expand=True, side="left")

        scrollbar = ttk.Scrollbar(self.log_container, orient="vertical", command=self.log_box.yview)
        scrollbar.pack(fill="y", side="right")
        self.log_box.configure(yscrollcommand=scrollbar.set)

        self.log_visible = False
        self._target_height = 240
        self._collapsed_height = 180

        self.root.geometry(f"420x{self._collapsed_height}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_logging(self):
        logging.basicConfig(
            filename=LOG_PATH,
            filemode="a",
            format="%(asctime)s [%(levelname)s] %(message)s",
            level=logging.INFO,
        )
        self.logger = logging.getLogger("launcher")

    def toggle_log(self):
        if self.log_visible:
            self._animate_height(self._collapsed_height)
            self.toggle_button.configure(text="LOG")
        else:
            self.log_container.pack(fill="both", expand=True, pady=(10, 0))
            self._animate_height(self._target_height)
            self.toggle_button.configure(text="HIDE")
        self.log_visible = not self.log_visible

    def _animate_height(self, target_height):
        current_width = self.root.winfo_width()
        try:
            current_height = self.root.winfo_height()
        except Exception:
            current_height = self._collapsed_height
        step = 8 if target_height > current_height else -8

        def step_height():
            nonlocal current_height
            current_height += step
            self.root.geometry(f"{current_width}x{current_height}")
            if (step > 0 and current_height < target_height) or (step < 0 and current_height > target_height):
                self.root.after(10, step_height)
            else:
                self.root.geometry(f"{current_width}x{target_height}")
                if target_height == self._collapsed_height and not self.log_visible:
                    self.log_container.pack_forget()

        step_height()

    def log(self, message, level="info"):
        if level == "error":
            self.logger.error(message)
        else:
            self.logger.info(message)

        self.steps.append(message)
        self.root.after(0, self._append_log, message)

    def _append_log(self, message):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_status(self, message):
        self.status_var.set(message)
        self.log(message)

    def open_log(self):
        try:
            if sys.platform.startswith("win"):
                os.startfile(LOG_PATH)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", LOG_PATH])
            else:
                subprocess.Popen(["xdg-open", LOG_PATH])
        except Exception as e:
            messagebox.showerror("Open Log", f"Unable to open log file:\n{e}")

    def on_close(self):
        if messagebox.askokcancel("Exit", "Stop the launcher and server?"):
            self.root.destroy()
            sys.exit(0)

    def start(self):
        threading.Thread(target=self.run_startup, daemon=True).start()
        self.root.mainloop()

    def run_startup(self):
        self.set_status("Starting application...")
        try:
            from app import create_app
            app = create_app()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.set_status("Failed to create application.")
            messagebox.showerror("Startup Error", f"Failed to create the app:\n{exc}")
            return

        host, port = "127.0.0.1", 5000
        self.set_status("Starting server...")
        self.server_thread = threading.Thread(target=self.serve_app, args=(app, host, port), daemon=True)
        self.server_thread.start()

        self.set_status("Opening browser...")
        time.sleep(1.2)
        try:
            webbrowser.open(f"http://{host}:{port}")
            self.set_status("Connected. Minimizing to taskbar...")
            self.progress.stop()
            self.root.after(500, self.root.iconify)
        except Exception as exc:
            self.set_status("Failed to open browser.")
            messagebox.showwarning("Browser", f"The server started, but the browser could not be opened:\n{exc}")

    def serve_app(self, app, host, port):
        try:
            from waitress import serve
            serve(app, host=host, port=port)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.set_status("Server stopped unexpectedly.")
            messagebox.showerror("Server Error", f"The server stopped unexpectedly:\n{exc}")


def main():
    if tk is None:
        print("Tkinter is required for the GUI launcher. Falling back to console mode.")
        run_console_launcher()
        return

    launcher = LauncherWindow()
    launcher.start()


def run_console_launcher():
    try:
        from app import create_app
        app = create_app()
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nAl-Qemma failed to start. Press Enter to close...")
        sys.exit(1)

    host, port = "127.0.0.1", 5000

    def open_browser():
        time.sleep(1.2)
        webbrowser.open(f"http://{host}:{port}")

    threading.Thread(target=open_browser, daemon=True).start()

    print("Al-Qemma is running.")
    print(f"If your browser didn't open automatically, go to http://{host}:{port}")
    print("Keep this window open while using Al-Qemma. Close it to stop the program.")

    try:
        from waitress import serve
        serve(app, host=host, port=port)
    except Exception:
        import traceback
        traceback.print_exc()
        input("\nAl-Qemma stopped unexpectedly. Press Enter to close...")


if __name__ == "__main__":
    main()
