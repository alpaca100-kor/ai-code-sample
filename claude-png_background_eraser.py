"""
PNG 투명 배경 편집기 (매직완드 스타일)
======================================
PNG 이미지를 불러와 클릭한 지점과 유사한 색상 영역을 투명하게 지워주는 도구입니다.
포토샵의 '매직완드(자동 선택)' 도구와 유사하게 동작하며, 감도(허용 오차)를 조절하여
정확히 일치하는 색상뿐 아니라 비슷한 색상 범위까지 한 번에 지울 수 있습니다.

[필요 패키지]
    pip install pillow numpy scipy sv-ttk
    (선택 사항) pip install tkinterdnd2   # 이미지 드래그 앤 드롭 지원

[주요 기능]
    - PNG 파일 열기 (버튼 / Ctrl+O / 창으로 드래그 앤 드롭)
    - 클릭 지점 기준 유사 색상 영역을 투명하게 변경 (매직완드)
    - 감도(허용 오차) 슬라이더로 선택 범위 조절 (0=완전 동일한 색상만 / 100=전체)
    - '인접 영역만 선택' 옵션
        켜짐: 클릭 지점과 맞닿아 연결된 영역만 지움
        꺼짐: 이미지 전체에서 조건에 맞는 색상을 모두 지움
    - 실행 취소 / 다시 실행 (Ctrl+Z / Ctrl+Y), 원본으로 초기화
    - 확대 / 축소 / 화면 크기에 맞춤 (Ctrl+휠로도 확대/축소 가능)
    - PNG로 저장 (투명도 유지, Ctrl+S)
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from PIL import Image, ImageTk
from scipy.ndimage import label

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False

try:
    import sv_ttk
    _SV_TTK_AVAILABLE = True
except ImportError:
    _SV_TTK_AVAILABLE = False


# ----------------------------------------------------------------------
# 순수 이미지 처리 로직
# (Tkinter GUI와 분리해두면 로직만 따로 테스트하기 쉽고, 재사용도 쉬워집니다)
# ----------------------------------------------------------------------

_MAX_RGB_DISTANCE = (255.0 ** 2 * 3) ** 0.5  # 두 색상 사이 최대 유클리드 거리 (약 441.67)
_CONNECTIVITY_4 = np.array([[0, 1, 0],
                             [1, 1, 1],
                             [0, 1, 0]])


def sensitivity_to_threshold(sensitivity):
    """감도값(0~100)을 실제 RGB 색상 거리 임계값(0~441.67)으로 변환합니다."""
    sensitivity = max(0.0, min(100.0, float(sensitivity)))
    return (sensitivity / 100.0) * _MAX_RGB_DISTANCE


def compute_magic_wand_mask(rgba_array, seed_x, seed_y, threshold, contiguous=True):
    """
    클릭한 지점(seed_x, seed_y)을 기준으로 투명하게 만들 픽셀의 마스크를 계산합니다.

    rgba_array : (H, W, 4) uint8 numpy 배열
    seed_x, seed_y : 클릭한 픽셀의 이미지 좌표 (x=열, y=행)
    threshold : 허용할 최대 색상 거리 (0이면 완전히 같은 색상만 포함)
    contiguous : True면 클릭 지점과 연결된 영역만, False면 이미지 전체에서
                 조건에 맞는 색상을 모두 선택합니다.

    반환값 : (H, W) 크기의 bool 배열. True인 픽셀을 투명하게 만들면 됩니다.
    """
    h, w = rgba_array.shape[:2]
    empty_result = np.zeros((h, w), dtype=bool)

    if not (0 <= seed_x < w and 0 <= seed_y < h):
        return empty_result

    alpha = rgba_array[:, :, 3]
    if alpha[seed_y, seed_x] == 0:
        return empty_result  # 이미 투명한 지점을 클릭한 경우 -> 변경할 것 없음

    seed_color = rgba_array[seed_y, seed_x, :3].astype(np.int32)
    rgb = rgba_array[:, :, :3].astype(np.int32)
    diff = rgb - seed_color
    dist = np.sqrt((diff.astype(np.float64) ** 2).sum(axis=2))

    candidate = (dist <= threshold) & (alpha > 0)

    if not contiguous:
        return candidate

    labeled, _ = label(candidate, structure=_CONNECTIVITY_4)
    target_label = labeled[seed_y, seed_x]
    if target_label == 0:
        return empty_result
    return labeled == target_label


def make_checkerboard_array(width, height, cell=10,
                             light=(235, 235, 235, 255), dark=(205, 205, 205, 255)):
    """투명 영역을 시각적으로 표시하기 위한 체크무늬 배경 배열을 생성합니다."""
    width = max(1, width)
    height = max(1, height)
    ys = (np.arange(height) // cell) % 2
    xs = (np.arange(width) // cell) % 2
    pattern = np.logical_xor(ys[:, None], xs[None, :])
    light_arr = np.array(light, dtype=np.uint8)
    dark_arr = np.array(dark, dtype=np.uint8)
    return np.where(pattern[..., None], light_arr, dark_arr).astype(np.uint8)


# ----------------------------------------------------------------------
# GUI 애플리케이션
# ----------------------------------------------------------------------

class MagicWandApp:
    MAX_UNDO = 15
    MIN_SCALE = 0.05
    MAX_SCALE = 20.0

    def __init__(self, root):
        self.root = root
        self.root.title("PNG 투명 배경 편집기 (매직완드)")
        self.root.geometry("1100x750")
        self.root.minsize(720, 520)

        self.file_path = None
        self.original_array = None      # 최초 로드 상태 (초기화용)
        self.current_array = None       # 현재 편집 상태
        self.undo_stack = []
        self.redo_stack = []

        self.scale = 1.0
        self.tk_image = None            # PhotoImage 참조 유지용 (GC 방지)
        self.canvas_image_id = None
        self.placeholder_id = None
        self._checker_cache = {}

        self.tolerance_var = tk.IntVar(value=20)
        self.contiguous_var = tk.BooleanVar(value=True)

        self._build_ui()
        self._bind_events()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------
    def _build_ui(self):
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(side="top", fill="x")

        ttk.Button(toolbar, text="파일 열기", command=self.open_file).pack(side="left", padx=(0, 4))
        self.save_btn = ttk.Button(toolbar, text="저장", command=self.save_file, state="disabled")
        self.save_btn.pack(side="left", padx=4)

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)

        self.undo_btn = ttk.Button(toolbar, text="↶ 실행 취소", command=self.undo, state="disabled")
        self.undo_btn.pack(side="left", padx=4)
        self.redo_btn = ttk.Button(toolbar, text="↷ 다시 실행", command=self.redo, state="disabled")
        self.redo_btn.pack(side="left", padx=4)
        self.reset_btn = ttk.Button(toolbar, text="초기화", command=self.reset_image, state="disabled")
        self.reset_btn.pack(side="left", padx=4)

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Label(toolbar, text="감도").pack(side="left", padx=(0, 4))
        self.tolerance_scale = ttk.Scale(
            toolbar, from_=0, to=100, orient="horizontal", length=150,
            variable=self.tolerance_var, command=self._on_tolerance_change
        )
        self.tolerance_scale.pack(side="left")
        self.tolerance_label = ttk.Label(toolbar, text="20", width=4)
        self.tolerance_label.pack(side="left", padx=(4, 8))

        ttk.Checkbutton(
            toolbar, text="인접 영역만 선택", variable=self.contiguous_var
        ).pack(side="left", padx=8)

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(toolbar, text="−", width=3, command=self.zoom_out).pack(side="left")
        self.zoom_label = ttk.Label(toolbar, text="100%", width=5, anchor="center")
        self.zoom_label.pack(side="left", padx=2)
        ttk.Button(toolbar, text="+", width=3, command=self.zoom_in).pack(side="left")
        ttk.Button(toolbar, text="화면 맞춤", command=self.fit_to_window).pack(side="left", padx=(4, 0))

        # 캔버스 영역 (스크롤바 포함)
        canvas_frame = ttk.Frame(self.root)
        canvas_frame.pack(side="top", fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame, bg="#3c3c3c", highlightthickness=0)
        vbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        hbar = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        self.placeholder_id = self.canvas.create_text(
            400, 300, text="파일 열기 버튼을 누르거나 PNG 파일을 이 창으로 드래그하세요",
            fill="#9a9a9a", font=("Segoe UI", 13)
        )

        # 상태 표시줄
        status = ttk.Frame(self.root, padding=(8, 4))
        status.pack(side="bottom", fill="x")
        self.status_label = ttk.Label(status, text="PNG 파일을 열어주세요.")
        self.status_label.pack(side="left")

        if _DND_AVAILABLE:
            try:
                self.canvas.drop_target_register(DND_FILES)
                self.canvas.dnd_bind('<<Drop>>', self._on_drop)
            except tk.TclError:
                # tkdnd Tcl 확장이 로드되지 않은 환경(루트가 TkinterDnD.Tk()가 아닌 경우 등).
                # 드래그 앤 드롭만 비활성화하고 나머지 기능은 정상 동작하도록 함.
                pass

    def _bind_events(self):
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)          # Windows / macOS
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel_linux_up)   # Linux
        self.canvas.bind("<Button-5>", self._on_mousewheel_linux_down)

        self.root.bind("<Control-o>", lambda e: self.open_file())
        self.root.bind("<Control-s>", lambda e: self.save_file())
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-y>", lambda e: self.redo())

    # ------------------------------------------------------------------
    # 파일 열기 / 저장
    # ------------------------------------------------------------------
    def open_file(self, path=None):
        if path is None:
            path = filedialog.askopenfilename(
                title="PNG 파일 열기",
                filetypes=[("PNG 파일", "*.png"), ("모든 파일", "*.*")]
            )
        if not path:
            return

        try:
            img = Image.open(path)
            img = img.convert("RGBA")
        except Exception as e:
            messagebox.showerror("오류", f"이미지를 여는 중 오류가 발생했습니다.\n\n{e}")
            return

        self.file_path = path
        self.original_array = np.array(img)
        self.current_array = self.original_array.copy()
        self.undo_stack.clear()
        self.redo_stack.clear()

        if self.placeholder_id is not None:
            self.canvas.itemconfig(self.placeholder_id, state="hidden")

        self.root.after(10, self.fit_to_window)  # 캔버스 크기 확정 후 맞춤 실행
        self._update_button_states()
        self._update_status()
        self.root.title(f"PNG 투명 배경 편집기 - {os.path.basename(path)}")

    def _on_drop(self, event):
        raw = event.data.strip()
        if raw.startswith('{') and raw.endswith('}'):
            raw = raw[1:-1]
        path = raw.split('} {')[0] if '} {' in raw else raw
        if path.lower().endswith(".png"):
            self.open_file(path)
        else:
            messagebox.showwarning("알림", "PNG 파일만 지원합니다.")

    def save_file(self):
        if self.current_array is None:
            return
        base = os.path.splitext(os.path.basename(self.file_path))[0] if self.file_path else "untitled"
        initial_dir = os.path.dirname(self.file_path) if self.file_path else os.getcwd()
        path = filedialog.asksaveasfilename(
            title="PNG로 저장",
            defaultextension=".png",
            filetypes=[("PNG 파일", "*.png")],
            initialdir=initial_dir,
            initialfile=f"{base}_transparent.png",
        )
        if not path:
            return
        try:
            Image.fromarray(self.current_array, mode="RGBA").save(path, "PNG")
        except Exception as e:
            messagebox.showerror("오류", f"저장 중 오류가 발생했습니다.\n\n{e}")
            return
        self._update_status(extra=f"저장 완료: {os.path.basename(path)}")

    # ------------------------------------------------------------------
    # 매직완드 편집
    # ------------------------------------------------------------------
    def _on_canvas_click(self, event):
        if self.current_array is None:
            return
        cx = self.canvas.canvasx(event.x)
        cy = self.canvas.canvasy(event.y)
        ix = int(cx / self.scale)
        iy = int(cy / self.scale)
        self._apply_magic_wand(ix, iy)

    def _push_undo_state(self):
        self.undo_stack.append(self.current_array.copy())
        if len(self.undo_stack) > self.MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _apply_magic_wand(self, x, y):
        arr = self.current_array
        h, w = arr.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return

        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            threshold = sensitivity_to_threshold(self.tolerance_var.get())
            mask = compute_magic_wand_mask(arr, x, y, threshold, contiguous=self.contiguous_var.get())
        finally:
            self.root.config(cursor="")

        if not mask.any():
            self._update_status(extra="선택된 영역이 없습니다 (이미 투명하거나 조건에 맞는 픽셀 없음)")
            return

        self._push_undo_state()
        new_arr = arr.copy()
        new_arr[mask, 3] = 0
        self.current_array = new_arr

        self._redraw_canvas()
        self._update_button_states()
        self._update_status(extra=f"{int(mask.sum()):,}개 픽셀을 투명하게 변경했습니다.")

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.current_array.copy())
        self.current_array = self.undo_stack.pop()
        self._redraw_canvas()
        self._update_button_states()
        self._update_status()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.current_array.copy())
        self.current_array = self.redo_stack.pop()
        self._redraw_canvas()
        self._update_button_states()
        self._update_status()

    def reset_image(self):
        if self.original_array is None:
            return
        if not messagebox.askyesno("초기화", "모든 편집 내용을 취소하고 원본 이미지로 되돌리시겠습니까?"):
            return
        self._push_undo_state()
        self.current_array = self.original_array.copy()
        self._redraw_canvas()
        self._update_button_states()
        self._update_status()

    # ------------------------------------------------------------------
    # 화면 표시 / 확대-축소
    # ------------------------------------------------------------------
    def _redraw_canvas(self):
        if self.current_array is None:
            return
        h, w = self.current_array.shape[:2]
        disp_w = max(1, round(w * self.scale))
        disp_h = max(1, round(h * self.scale))

        pil_img = Image.fromarray(self.current_array, mode="RGBA")
        if disp_w != w or disp_h != h:
            resample = Image.Resampling.NEAREST if self.scale >= 1 else Image.Resampling.BILINEAR
            pil_img = pil_img.resize((disp_w, disp_h), resample)

        checker = self._get_checkerboard(disp_w, disp_h)
        composed = Image.alpha_composite(checker, pil_img)

        self.tk_image = ImageTk.PhotoImage(composed)
        if self.canvas_image_id is None:
            self.canvas_image_id = self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        else:
            self.canvas.itemconfig(self.canvas_image_id, image=self.tk_image)
        self.canvas.config(scrollregion=(0, 0, disp_w, disp_h))

    def _get_checkerboard(self, w, h):
        key = (w, h)
        if key not in self._checker_cache:
            self._checker_cache.clear()  # 최근 크기 하나만 캐시 (메모리 절약)
            arr = make_checkerboard_array(w, h)
            self._checker_cache[key] = Image.fromarray(arr, mode="RGBA")
        return self._checker_cache[key]

    def zoom_in(self):
        self._set_scale(self.scale * 1.25)

    def zoom_out(self):
        self._set_scale(self.scale / 1.25)

    def fit_to_window(self):
        if self.current_array is None:
            return
        h, w = self.current_array.shape[:2]
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        if canvas_w <= 1:
            canvas_w = 800
        if canvas_h <= 1:
            canvas_h = 600
        margin = 20
        scale = min((canvas_w - margin) / w, (canvas_h - margin) / h)
        self._set_scale(scale)

    def _set_scale(self, new_scale):
        new_scale = max(self.MIN_SCALE, min(new_scale, self.MAX_SCALE))
        self.scale = new_scale
        self._redraw_canvas()
        self.zoom_label.config(text=f"{int(round(self.scale * 100))}%")
        self._update_status()

    def _on_canvas_resize(self, event):
        if self.current_array is None and self.placeholder_id is not None:
            self.canvas.coords(self.placeholder_id, event.width // 2, event.height // 2)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_shift_mousewheel(self, event):
        self.canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_ctrl_mousewheel(self, event):
        if event.delta > 0:
            self.zoom_in()
        else:
            self.zoom_out()

    def _on_mousewheel_linux_up(self, event):
        self.canvas.yview_scroll(-1, "units")

    def _on_mousewheel_linux_down(self, event):
        self.canvas.yview_scroll(1, "units")

    # ------------------------------------------------------------------
    # 기타 UI 헬퍼
    # ------------------------------------------------------------------
    def _on_tolerance_change(self, value):
        self.tolerance_label.config(text=str(int(float(value))))

    def _update_button_states(self):
        has_image = self.current_array is not None
        self.save_btn.config(state="normal" if has_image else "disabled")
        self.reset_btn.config(state="normal" if has_image else "disabled")
        self.undo_btn.config(state="normal" if self.undo_stack else "disabled")
        self.redo_btn.config(state="normal" if self.redo_stack else "disabled")

    def _update_status(self, extra=None):
        if self.current_array is None:
            self.status_label.config(text="PNG 파일을 열어주세요.")
            return
        h, w = self.current_array.shape[:2]
        name = os.path.basename(self.file_path) if self.file_path else "제목 없음"
        text = f"{name}  |  {w} x {h}px  |  확대/축소 {int(round(self.scale * 100))}%"
        if extra:
            text += f"  |  {extra}"
        self.status_label.config(text=text)


def main():
    root = TkinterDnD.Tk() if _DND_AVAILABLE else tk.Tk()

    if _SV_TTK_AVAILABLE:
        sv_ttk.set_theme("dark")

    app = MagicWandApp(root)

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]) and sys.argv[1].lower().endswith(".png"):
        root.after(100, lambda: app.open_file(sys.argv[1]))

    root.mainloop()


if __name__ == "__main__":
    main()
