#!/usr/bin/env python3
"""
PDF → JPG 배치 변환 프로그램

기능 요약:
- 총 페이지 수 기준 ProgressBar
- 변환 작업 스레드 분리 (GUI 프리징 방지) + 파일 단위 병렬 변환
- PDF 개별 페이지 오류 처리, error_log.txt 자동 생성
- OS별 출력 폴더 열기 안정화
- 변환 취소, GUI 내 로그창
- 출력 폴더명 유효성 검사, 비밀번호 보호 PDF 감지, 파일명 충돌 방지

[이번 업데이트]
1. 폴더 전체 변환 / 개별 파일 선택을 라디오 버튼으로 명확히 구분하는 UI로 개선.
   개별 파일 선택 모드에서는 "일부 페이지만 변환" 옵션을 제공하여
   PDF 페이지 중 원하는 페이지만 골라 이미지로 변환할 수 있음 (예: 1-3,5,7-9).
2. 드래그 앤 드롭으로 폴더 또는 PDF 파일(복수 선택 가능)을 바로 추가할 수 있는
   드롭존을 추가. tkinterdnd2가 설치되어 있지 않으면 자동으로 비활성화되고
   기존 버튼 기반 UI만 사용 가능 (pip install tkinterdnd2 로 설치).
"""

import os
import sys
import threading
import subprocess
import fitz  # PyMuPDF
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


DEFAULT_OUTPUT_FOLDER = "converted files"
AVAILABLE_DPI = [72, 150, 200, 300, 400]
MAX_WORKERS = min(4, (os.cpu_count() or 1))
PLACEHOLDER_RANGE = "예: 1-3,5,7-9"

# Windows/맥/리눅스 공통으로 폴더명에 쓰기 곤란한 문자들
INVALID_FOLDERNAME_CHARS = '<>:"/\\|?*'


def sanitize_foldername(name: str) -> str:
    """
    출력 폴더명으로 사용하기 안전한 문자열로 정리한다.
    - OS에서 금지된 문자 제거
    - '..' 같은 상위 경로 이탈 패턴 제거
    - 앞뒤 공백/마침표 제거 (Windows 폴더명 제약)
    - 결과가 비면 기본값으로 대체
    """
    if not name:
        return DEFAULT_OUTPUT_FOLDER

    cleaned = name
    for ch in INVALID_FOLDERNAME_CHARS:
        cleaned = cleaned.replace(ch, "_")
    cleaned = cleaned.replace("..", "_")
    cleaned = cleaned.strip(" .")

    return cleaned or DEFAULT_OUTPUT_FOLDER


def parse_page_range(spec: str):
    """
    '1-3,5,7-9' 형식의 문자열을 1-indexed 페이지 번호의 정렬된 리스트로 변환한다.
    형식이 잘못되면 ValueError를 발생시킨다.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("범위를 입력하세요 (예: 1-3,5,7-9)")

    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            bounds = part.split("-")
            if len(bounds) != 2:
                raise ValueError(f"잘못된 범위 형식: '{part}'")
            try:
                start, end = int(bounds[0].strip()), int(bounds[1].strip())
            except ValueError:
                raise ValueError(f"숫자가 아닙니다: '{part}'")
            if start < 1 or end < start:
                raise ValueError(f"잘못된 범위: '{part}'")
            pages.update(range(start, end + 1))
        else:
            try:
                n = int(part)
            except ValueError:
                raise ValueError(f"숫자가 아닙니다: '{part}'")
            if n < 1:
                raise ValueError(f"1 이상의 페이지 번호를 입력하세요: '{part}'")
            pages.add(n)

    if not pages:
        raise ValueError("유효한 페이지 번호가 없습니다.")

    return sorted(pages)


def collect_pdf_paths_from_drop(paths):
    """
    드래그 앤 드롭으로 들어온 경로 목록에서 PDF 파일 경로만 모은다.
    폴더가 포함되어 있으면 해당 폴더 최상위의 PDF 파일들을 포함시킨다.
    반환값: (pdf_paths, ignored_items)
    """
    pdf_paths = []
    ignored = []

    for p in paths:
        if os.path.isdir(p):
            try:
                for f in sorted(os.listdir(p)):
                    fp = os.path.join(p, f)
                    if f.lower().endswith(".pdf") and os.path.isfile(fp):
                        pdf_paths.append(fp)
            except Exception:
                ignored.append(p)
        elif os.path.isfile(p):
            if p.lower().endswith(".pdf"):
                pdf_paths.append(p)
            else:
                ignored.append(os.path.basename(p))
        else:
            ignored.append(p)

    return pdf_paths, ignored


def convert_pdf(pdf_path: str, out_dir: str, dpi: int, progress_callback=None,
                 cancel_event: threading.Event = None,
                 used_names: set = None, names_lock: threading.Lock = None,
                 page_indices=None):
    """
    PDF를 JPG 이미지로 변환.

    progress_callback: 페이지 변환 1회마다 호출되는 함수 (워커 스레드에서 호출될 수 있음)
    cancel_event: set되어 있으면 남은 페이지 변환을 중단
    used_names/names_lock: 서로 다른 폴더에서 온 동일 파일명 충돌을 피하기 위한 공유 상태
    page_indices: 0-indexed 페이지 번호 리스트. None이면 전체 페이지 변환.
                  범위를 벗어난 인덱스는 무시되고 실패 로그에 기록된다.
    """
    doc = fitz.open(pdf_path)

    if doc.needs_pass:
        doc.close()
        raise ValueError("비밀번호로 보호된 PDF입니다 (건너뜀)")

    base = os.path.splitext(os.path.basename(pdf_path))[0]
    n_pages = doc.page_count

    if n_pages == 0:
        doc.close()
        return 0, []

    fail_pages = []

    if page_indices is None:
        indices = list(range(n_pages))
        partial_mode = False
    else:
        indices = [i for i in page_indices if 0 <= i < n_pages]
        out_of_range = sorted({i + 1 for i in page_indices if i < 0 or i >= n_pages})
        for pg in out_of_range:
            fail_pages.append((pg, f"요청한 페이지가 존재하지 않음 (총 {n_pages}페이지)"))
        partial_mode = True

    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)

    produced = 0
    cancelled = False
    remaining = 0

    for pos, page_i in enumerate(indices):
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            remaining = len(indices) - pos
            break

        try:
            page = doc.load_page(page_i)
            pix = page.get_pixmap(matrix=matrix, alpha=False)

            if partial_mode:
                out_name = f"{base}_p{page_i + 1}.jpg"
            elif n_pages == 1:
                out_name = f"{base}.jpg"
            else:
                out_name = f"{base}_{page_i + 1}.jpg"

            # 서로 다른 폴더에서 온 동일 파일명 충돌 방지
            if used_names is not None:
                with names_lock:
                    candidate = out_name
                    counter = 1
                    while candidate in used_names:
                        stem, ext = os.path.splitext(out_name)
                        candidate = f"{stem}_dup{counter}{ext}"
                        counter += 1
                    used_names.add(candidate)
                out_name = candidate

            pix.save(os.path.join(out_dir, out_name))
            produced += 1

        except Exception as e:
            fail_pages.append((page_i + 1, str(e)))

        finally:
            if progress_callback:
                progress_callback()

    doc.close()

    if cancelled:
        fail_pages.append(("취소", f"{remaining}개 페이지 처리 전 취소됨"))

    return produced, fail_pages


class PDFConverterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF to JPG Converter")
        self.root.geometry("680x720")
        self.root.minsize(680, 620)
        self.root.resizable(True, True)

        self.worker_thread = None
        self.cancel_event = threading.Event()
        self.selected_files = []  # 개별 파일 선택 모드일 때 채워짐
        self.last_output_dir = None

        self.mode_var = tk.StringVar(value="folder")
        self.range_enabled_var = tk.BooleanVar(value=False)

        self.create_widgets()
        self._on_mode_change()

    # ------------------------------------------------------------------
    # 위젯 생성
    # ------------------------------------------------------------------
    def create_widgets(self):
        padx = 12
        pady = 6

        # 드래그 앤 드롭 존
        dnd_text = "📂 PDF 파일 또는 폴더를 여기로 끌어다 놓으세요"
        if not DND_AVAILABLE:
            dnd_text += "\n(tkinterdnd2 미설치로 비활성화 — pip install tkinterdnd2)"

        self.lbl_dropzone = tk.Label(
            self.root, text=dnd_text, relief="ridge", bd=2,
            bg="#f7f7f7", fg="#555555" if DND_AVAILABLE else "#aaaaaa",
            height=3, justify="center"
        )
        self.lbl_dropzone.pack(fill="x", padx=padx, pady=(pady, pady))

        if DND_AVAILABLE:
            self.lbl_dropzone.drop_target_register(DND_FILES)
            self.lbl_dropzone.dnd_bind("<<Drop>>", self.on_drop)

        # 입력 선택 (폴더 전체 vs 개별 파일)
        frm_mode = tk.LabelFrame(self.root, text="입력 선택", padx=10, pady=8)
        frm_mode.pack(fill="x", padx=padx, pady=(0, pady))

        frm_radio = tk.Frame(frm_mode)
        frm_radio.pack(fill="x")
        tk.Radiobutton(
            frm_radio, text="폴더 전체 변환", variable=self.mode_var, value="folder",
            command=self._on_mode_change
        ).pack(side="left")
        tk.Radiobutton(
            frm_radio, text="개별 파일 선택", variable=self.mode_var, value="files",
            command=self._on_mode_change
        ).pack(side="left", padx=(20, 0))

        ttk.Separator(frm_mode, orient="horizontal").pack(fill="x", pady=8)

        # --- 폴더 모드 UI (mode == "folder" 일 때만 표시됨) ---
        self.frm_folder_mode = tk.Frame(frm_mode)

        tk.Label(self.frm_folder_mode, text="PDF 폴더 경로:").pack(anchor="w")
        frm_folder_row = tk.Frame(self.frm_folder_mode)
        frm_folder_row.pack(fill="x")

        self.entry_folder = tk.Entry(frm_folder_row)
        self.entry_folder.pack(side="left", fill="x", expand=True)
        self.btn_browse_folder = tk.Button(frm_folder_row, text="찾기", width=10, command=self.browse_folder)
        self.btn_browse_folder.pack(side="left", padx=6)

        # --- 개별 파일 모드 UI (mode == "files" 일 때만 표시됨) ---
        self.frm_file_mode = tk.Frame(frm_mode)

        frm_file_row = tk.Frame(self.frm_file_mode)
        frm_file_row.pack(fill="x")

        self.btn_browse_files = tk.Button(frm_file_row, text="파일 선택", width=12, command=self.browse_files)
        self.btn_browse_files.pack(side="left")
        self.btn_remove_selected = tk.Button(
            frm_file_row, text="선택 항목 제거", width=12, command=self.remove_selected_file
        )
        self.btn_remove_selected.pack(side="left", padx=6)
        self.btn_clear_files = tk.Button(frm_file_row, text="목록 전체 삭제", width=11, command=self.clear_selected_files)
        self.btn_clear_files.pack(side="left", padx=6)

        self.lbl_selected_files = tk.Label(self.frm_file_mode, text="선택된 파일 없음")
        self.lbl_selected_files.pack(anchor="w", pady=(4, 0))

        # 선택된 PDF 파일의 실제 경로를 보여주는 목록
        frm_file_list = tk.Frame(self.frm_file_mode)
        frm_file_list.pack(fill="x", pady=(4, 0))

        list_scrollbar = tk.Scrollbar(frm_file_list)
        list_scrollbar.pack(side="right", fill="y")

        self.listbox_files = tk.Listbox(
            frm_file_list, height=5, selectmode="extended",
            yscrollcommand=list_scrollbar.set
        )
        self.listbox_files.pack(side="left", fill="x", expand=True)
        list_scrollbar.config(command=self.listbox_files.yview)

        frm_range = tk.Frame(self.frm_file_mode)
        frm_range.pack(fill="x", pady=(8, 0))

        self.chk_range = tk.Checkbutton(
            frm_range, text="일부 페이지만 변환", variable=self.range_enabled_var,
            command=self._on_range_toggle
        )
        self.chk_range.pack(side="left")

        self.entry_range = tk.Entry(frm_range, width=26)
        self.entry_range.pack(side="left", padx=6)
        self.entry_range.insert(0, PLACEHOLDER_RANGE)
        self.entry_range.config(fg="#999999")
        self.entry_range.bind("<FocusIn>", self._on_range_entry_focus_in)
        self.entry_range.bind("<FocusOut>", self._on_range_entry_focus_out)

        tk.Label(
            self.frm_file_mode,
            text="※ 여러 파일 선택 시, 지정한 페이지 범위가 각 파일에 동일하게 적용됩니다.",
            fg="#888888", font=("", 8)
        ).pack(anchor="w", pady=(4, 0))

        # DPI
        tk.Label(self.root, text="DPI 설정:").pack(anchor="w", padx=padx, pady=(pady, 0))
        self.combo_dpi = ttk.Combobox(self.root, values=[str(x) for x in AVAILABLE_DPI], state="readonly")
        self.combo_dpi.set("300")
        self.combo_dpi.pack(fill="x", padx=padx)

        # 출력 폴더명
        tk.Label(self.root, text="출력 폴더명:").pack(anchor="w", padx=padx, pady=(pady, 0))
        self.entry_out = tk.Entry(self.root)
        self.entry_out.insert(0, DEFAULT_OUTPUT_FOLDER)
        self.entry_out.pack(fill="x", padx=padx)

        # 진행상황
        self.lbl_progress = tk.Label(self.root, text="진행 상황: 대기 중")
        self.lbl_progress.pack(anchor="w", padx=padx, pady=(pady, 0))

        self.progress = ttk.Progressbar(self.root, length=520, mode="determinate")
        self.progress.pack(fill="x", padx=padx, pady=8)

        # 버튼
        frm_btn = tk.Frame(self.root)
        frm_btn.pack(fill="x", padx=padx)

        self.btn_start = tk.Button(frm_btn, text="변환 시작", width=12, command=self.start_thread)
        self.btn_start.pack(side="left")

        self.btn_cancel = tk.Button(frm_btn, text="취소", width=12, command=self.cancel_conversion, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)

        tk.Button(frm_btn, text="출력 폴더 열기", width=14, command=self.open_output_folder).pack(side="left", padx=6)
        tk.Button(frm_btn, text="종료", width=12, command=self.root.quit).pack(side="right")

        # 로그창
        tk.Label(self.root, text="처리 로그:").pack(anchor="w", padx=padx, pady=(pady, 0))

        frm_log = tk.Frame(self.root)
        frm_log.pack(fill="both", expand=True, padx=padx, pady=(0, pady))

        scrollbar = tk.Scrollbar(frm_log)
        scrollbar.pack(side="right", fill="y")

        self.txt_log = tk.Text(frm_log, height=8, wrap="word", yscrollcommand=scrollbar.set, state="disabled")
        self.txt_log.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.txt_log.yview)

        tk.Button(self.root, text="로그 지우기", width=12, command=self.clear_log).pack(anchor="e", padx=padx, pady=(0, pady))

    # ------------------------------------------------------------------
    # 입력 모드 전환 / 페이지 범위 UI 상태 관리
    # ------------------------------------------------------------------
    def _on_mode_change(self):
        """
        선택하지 않은 쪽의 UI는 비활성화(회색)로 남겨두지 않고 아예 화면에서 숨겨서,
        사용자가 실제로 조작 가능한 영역만 보이도록 한다.
        """
        mode = self.mode_var.get()

        self.frm_folder_mode.pack_forget()
        self.frm_file_mode.pack_forget()

        if mode == "folder":
            self.frm_folder_mode.pack(fill="x", pady=(4, 0))
        else:
            self.frm_file_mode.pack(fill="x", pady=(4, 0))

        self._refresh_selected_files_ui()
        self._on_range_toggle()

    def _on_range_toggle(self):
        if self.mode_var.get() == "files" and self.range_enabled_var.get():
            self.entry_range.config(state="normal")
        else:
            self.entry_range.config(state="disabled")

    def _on_range_entry_focus_in(self, _event):
        if self.entry_range.get() == PLACEHOLDER_RANGE:
            self.entry_range.delete(0, tk.END)
            self.entry_range.config(fg="black")

    def _on_range_entry_focus_out(self, _event):
        if not self.entry_range.get().strip():
            self.entry_range.insert(0, PLACEHOLDER_RANGE)
            self.entry_range.config(fg="#999999")

    def _get_range_text(self):
        text = self.entry_range.get().strip()
        return "" if text == PLACEHOLDER_RANGE else text

    # ------------------------------------------------------------------
    # 입력 선택 (버튼)
    # ------------------------------------------------------------------
    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.entry_folder.delete(0, tk.END)
            self.entry_folder.insert(0, folder)

    def browse_files(self):
        paths = filedialog.askopenfilenames(filetypes=[("PDF 파일", "*.pdf")])
        if paths:
            for p in paths:
                if p not in self.selected_files:
                    self.selected_files.append(p)
            self._refresh_selected_files_ui()

    def remove_selected_file(self):
        """파일 목록에서 사용자가 선택(하이라이트)한 항목만 제거한다."""
        indices = list(self.listbox_files.curselection())
        if not indices:
            return
        for idx in reversed(indices):
            del self.selected_files[idx]
        self._refresh_selected_files_ui()

    def clear_selected_files(self):
        self.selected_files = []
        self._refresh_selected_files_ui()

    def _refresh_selected_files_ui(self):
        """선택된 파일 개수 라벨과 경로 리스트박스를 현재 상태에 맞게 갱신한다."""
        self.listbox_files.delete(0, tk.END)
        for p in self.selected_files:
            self.listbox_files.insert(tk.END, p)

        if self.selected_files:
            self.lbl_selected_files.config(text=f"{len(self.selected_files)}개 파일 선택됨", fg="#0a6e0a")
        else:
            self.lbl_selected_files.config(text="선택된 파일 없음", fg="#999999")

    # ------------------------------------------------------------------
    # 드래그 앤 드롭
    # ------------------------------------------------------------------
    def on_drop(self, event):
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            return

        if not paths:
            return

        # 폴더 하나만 드롭한 경우 -> "폴더 전체 변환" 라디오 버튼 활성화
        if len(paths) == 1 and os.path.isdir(paths[0]):
            self.mode_var.set("folder")
            self.entry_folder.delete(0, tk.END)
            self.entry_folder.insert(0, paths[0])
            self.selected_files = []
            self._on_mode_change()
            self._append_log(f">> 폴더 드롭됨: {paths[0]}")
            return

        # 그 외 (PDF 파일 여러 개, 또는 폴더+파일 혼합) -> "개별 파일 선택" 라디오 버튼 활성화
        pdf_paths, ignored = collect_pdf_paths_from_drop(paths)
        if not pdf_paths:
            messagebox.showwarning("알림", "드롭한 항목에서 PDF 파일을 찾을 수 없습니다.")
            return

        self.mode_var.set("files")
        for p in pdf_paths:
            if p not in self.selected_files:
                self.selected_files.append(p)
        self._on_mode_change()
        self._append_log(f">> 드래그 앤 드롭으로 {len(pdf_paths)}개 PDF 파일 추가됨")
        if ignored:
            self._append_log(f"   (PDF가 아니어서 제외된 항목 {len(ignored)}개)")

    def open_output_folder(self):
        output_dir = self.last_output_dir

        if not output_dir:
            out_name = sanitize_foldername(self.entry_out.get().strip())
            if self.mode_var.get() == "files" and self.selected_files:
                base_folder = os.path.dirname(self.selected_files[0])
                output_dir = os.path.join(base_folder, out_name)
            else:
                folder = self.entry_folder.get().strip()
                output_dir = os.path.join(folder, out_name) if folder else ""

        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showinfo("정보", "출력 폴더가 없습니다. 먼저 변환을 실행하세요.")
            return

        try:
            if os.name == "nt":
                os.startfile(output_dir)
            elif sys.platform == "darwin":
                subprocess.call(["open", output_dir])
            else:
                subprocess.call(["xdg-open", output_dir])
        except Exception:
            messagebox.showinfo("정보", f"경로: {output_dir}")

    # ------------------------------------------------------------------
    # 스레드 → 메인 스레드 GUI 갱신 헬퍼
    # (워커/워커풀 스레드에서 위젯을 직접 건드리지 않고 항상 after()로 위임)
    # ------------------------------------------------------------------
    def _set_progress_label(self, text: str):
        self.root.after(0, lambda: self.lbl_progress.config(text=text))

    def _set_progress_value(self, value: int, maximum: int = None):
        def _apply():
            if maximum is not None:
                self.progress["maximum"] = maximum
            self.progress["value"] = value
        self.root.after(0, _apply)

    def _set_start_button_state(self, state: str):
        self.root.after(0, lambda: self.btn_start.config(state=state))

    def _set_cancel_button_state(self, state: str):
        self.root.after(0, lambda: self.btn_cancel.config(state=state))

    def _show_messagebox(self, kind: str, title: str, message: str):
        fn = {"error": messagebox.showerror, "info": messagebox.showinfo, "warning": messagebox.showwarning}[kind]
        self.root.after(0, lambda: fn(title, message))

    def _append_log(self, text: str):
        def _do():
            self.txt_log.config(state="normal")
            self.txt_log.insert("end", text + "\n")
            self.txt_log.see("end")
            self.txt_log.config(state="disabled")
        self.root.after(0, _do)

    def clear_log(self):
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")

    # ------------------------------------------------------------------
    # 변환 제어
    # ------------------------------------------------------------------
    def start_thread(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self._show_messagebox("info", "정보", "이미 작업이 진행 중입니다.")
            return

        self.cancel_event.clear()
        self.worker_thread = threading.Thread(target=self.start_conversion, daemon=True)
        self.worker_thread.start()

    def cancel_conversion(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.cancel_event.set()
            self._set_progress_label("취소 중... 진행 중인 페이지 완료 후 중단됩니다")
            self._append_log(">> 사용자가 취소를 요청했습니다. 진행 중인 파일만 마무리합니다.")
            self._set_cancel_button_state("disabled")

    def start_conversion(self):
        self._set_start_button_state("disabled")
        self._set_cancel_button_state("normal")

        mode = self.mode_var.get()
        range_pages = None  # 1-indexed 페이지 번호 리스트 (개별 파일 + 범위 지정 모드일 때만 사용)

        # ------------------------------------------------------------
        # 대상 파일 목록 구성
        # ------------------------------------------------------------
        if mode == "files":
            if not self.selected_files:
                self._show_messagebox("error", "오류", "개별 파일 모드입니다. 변환할 PDF 파일을 선택하세요.")
                self._set_start_button_state("normal")
                self._set_cancel_button_state("disabled")
                return

            job_paths = list(self.selected_files)
            invalid = [p for p in job_paths if not os.path.isfile(p)]
            if invalid:
                self._show_messagebox("error", "오류", "선택한 파일 중 접근할 수 없는 파일이 있습니다.")
                self._set_start_button_state("normal")
                self._set_cancel_button_state("disabled")
                return
            base_folder = os.path.dirname(job_paths[0])

            if self.range_enabled_var.get():
                try:
                    range_pages = parse_page_range(self._get_range_text())
                except ValueError as e:
                    self._show_messagebox("error", "오류", f"페이지 범위가 올바르지 않습니다.\n{e}")
                    self._set_start_button_state("normal")
                    self._set_cancel_button_state("disabled")
                    return
        else:
            folder = self.entry_folder.get().strip()
            if not folder or not os.path.isdir(folder):
                self._show_messagebox("error", "오류", "올바른 폴더를 선택하세요.")
                self._set_start_button_state("normal")
                self._set_cancel_button_state("disabled")
                return
            job_paths = [
                os.path.join(folder, f) for f in os.listdir(folder)
                if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(folder, f))
            ]
            base_folder = folder

        if not job_paths:
            self._show_messagebox("info", "알림", "PDF 파일이 없습니다.")
            self._set_start_button_state("normal")
            self._set_cancel_button_state("disabled")
            return

        try:
            dpi = int(self.combo_dpi.get())
        except Exception:
            self._show_messagebox("error", "오류", "유효한 DPI 값을 선택하세요.")
            self._set_start_button_state("normal")
            self._set_cancel_button_state("disabled")
            return

        out_name = sanitize_foldername(self.entry_out.get().strip())
        output_dir = os.path.join(base_folder, out_name)
        os.makedirs(output_dir, exist_ok=True)
        self.last_output_dir = output_dir

        range_note = f", 페이지 범위: {self._get_range_text()}" if range_pages else ""
        self._append_log(f"=== 변환 시작: {len(job_paths)}개 파일, DPI {dpi}{range_note} ===")

        # ------------------------------------------------------------
        # 총 페이지 수 계산 + 사전 검증
        # (손상 파일 / 비밀번호 보호 파일 / 범위를 벗어난 페이지 걸러내기)
        # ------------------------------------------------------------
        page_counts = {}
        file_page_indices = {}
        broken_files = []

        for path in job_paths:
            fname = os.path.basename(path)
            try:
                doc = fitz.open(path)
                if doc.needs_pass:
                    broken_files.append(f"{fname}: 비밀번호로 보호된 PDF (건너뜀)")
                    doc.close()
                    continue

                n_pages = doc.page_count
                doc.close()

                if range_pages is not None:
                    indices = [p - 1 for p in range_pages if 1 <= p <= n_pages]
                    out_of_range = [p for p in range_pages if p > n_pages]
                    if out_of_range:
                        broken_files.append(
                            f"{fname}: 요청한 페이지 {out_of_range} 는 총 {n_pages}페이지를 초과하여 제외됨"
                        )
                    if not indices:
                        broken_files.append(f"{fname}: 지정한 범위에 해당하는 페이지가 없어 건너뜀")
                        continue
                    file_page_indices[path] = indices
                else:
                    indices = list(range(n_pages))
                    file_page_indices[path] = None  # None = 전체 변환

                page_counts[path] = len(indices)

            except Exception as e:
                broken_files.append(f"{fname}: 파일을 열 수 없음 ({e})")

        for line in broken_files:
            self._append_log(f"[사전 검증] {line}")

        valid_paths = list(page_counts.keys())
        total_pages = sum(page_counts.values())

        if total_pages == 0:
            self._show_messagebox("info", "알림", "변환 가능한 PDF 페이지가 없습니다.")
            self._set_start_button_state("normal")
            self._set_cancel_button_state("disabled")
            return

        # ProgressBar 초기화
        self._set_progress_value(0, maximum=total_pages)
        self._set_progress_label(f"진행 상황: 0 / {total_pages} 페이지 처리 중")

        # 진행 카운터 (여러 스레드에서 동시에 갱신되므로 Lock으로 보호)
        progress_lock = threading.Lock()
        current_page = 0

        def update_progress():
            nonlocal current_page
            with progress_lock:
                current_page += 1
                value = current_page
            self._set_progress_value(value)
            self._set_progress_label(f"진행 상황: {value} / {total_pages} 페이지 처리 중")

        fail_logs = list(broken_files)
        fail_logs_lock = threading.Lock()
        success_files = 0
        success_lock = threading.Lock()

        # 서로 다른 폴더에서 온 파일명 충돌 방지용 공유 상태
        used_names = set()
        names_lock = threading.Lock()

        # ------------------------------------------------------------
        # 파일 단위 병렬 변환 (ThreadPoolExecutor), 취소 요청 시 조기 중단
        # ------------------------------------------------------------
        def convert_one(path):
            fname = os.path.basename(path)

            if self.cancel_event.is_set():
                with fail_logs_lock:
                    fail_logs.append(f"{fname}: 취소로 인해 시작되지 않음")
                return False

            try:
                produced, fail_pages = convert_pdf(
                    path, output_dir, dpi,
                    progress_callback=update_progress,
                    cancel_event=self.cancel_event,
                    used_names=used_names,
                    names_lock=names_lock,
                    page_indices=file_page_indices.get(path),
                )
                with fail_logs_lock:
                    for pg, msg in fail_pages:
                        fail_logs.append(f"{fname} - 페이지 {pg}: {msg}")
                self._append_log(f"[완료] {fname}: {produced}페이지 성공")
                return True
            except Exception as e:
                with fail_logs_lock:
                    fail_logs.append(f"{fname}: {e}")
                self._append_log(f"[실패] {fname}: {e}")
                return False

        if valid_paths:
            worker_count = min(MAX_WORKERS, len(valid_paths))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {executor.submit(convert_one, path): path for path in valid_paths}
                for future in as_completed(futures):
                    ok = future.result()
                    if ok:
                        with success_lock:
                            success_files += 1
                    if self.cancel_event.is_set():
                        for f in futures:
                            f.cancel()

        # 오류 로그 저장
        if fail_logs:
            with open(os.path.join(output_dir, "error_log.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(fail_logs))

        was_cancelled = self.cancel_event.is_set()

        # 결과 안내
        if was_cancelled:
            self._append_log(f"=== 취소됨: {success_files}/{len(job_paths)}개 파일 처리 완료 ===")
            self._show_messagebox(
                "warning", "취소됨",
                f"작업이 취소되었습니다.\n"
                f"총 {len(job_paths)}개 파일 중 {success_files}개 처리 완료.\n\n"
                f"출력 경로:\n{output_dir}"
            )
        elif fail_logs:
            self._append_log(f"=== 완료(일부 실패): {success_files}/{len(job_paths)}개 파일 성공 ===")
            self._show_messagebox(
                "warning", "완료(일부 실패)",
                f"총 {len(job_paths)}개 파일 중 {success_files}개 처리 성공.\n"
                f"총 페이지 수: {total_pages}\n"
                f"일부 파일/페이지에서 오류가 발생했습니다.\n"
                f"error_log.txt 및 로그창을 확인하세요.\n\n"
                f"출력 경로:\n{output_dir}"
            )
        else:
            self._append_log(f"=== 완료: {len(job_paths)}개 파일 모두 성공 ===")
            self._show_messagebox(
                "info", "완료",
                f"모든 파일 변환 완료!\n"
                f"총 파일: {len(job_paths)}\n"
                f"총 페이지: {total_pages}\n\n"
                f"출력 경로:\n{output_dir}"
            )

        self._set_progress_label("취소됨" if was_cancelled else "진행 상황: 완료")
        self._set_start_button_state("normal")
        self._set_cancel_button_state("disabled")


if __name__ == "__main__":
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
        print("[알림] tkinterdnd2가 설치되어 있지 않아 드래그 앤 드롭 기능이 비활성화됩니다.")
        print("       'pip install tkinterdnd2' 명령으로 설치하면 사용할 수 있습니다.")

    app = PDFConverterGUI(root)
    root.mainloop()
