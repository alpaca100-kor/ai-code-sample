import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import cv2
import numpy as np

class MagicWandApp:
    def __init__(self, root):
        self.root = root
        self.root.title("투명 배경 만들기 (Magic Wand)")
        self.root.geometry("900x700")

        # 상태 변수
        self.original_image = None  # 원본 이미지 (수정용, RGBA)
        self.tk_image = None        # 화면 표시용 이미지
        self.history = []           # 실행 취소를 위한 히스토리 저장
        
        self.scale_factor = 1.0
        self.offset_x = 0
        self.offset_y = 0

        self.setup_ui()

    def setup_ui(self):
        # 상단 컨트롤 패널
        control_frame = tk.Frame(self.root, pady=10)
        control_frame.pack(fill=tk.X)

        btn_load = tk.Button(control_frame, text="이미지 불러오기", command=self.load_image, width=15)
        btn_load.pack(side=tk.LEFT, padx=10)

        btn_save = tk.Button(control_frame, text="이미지 저장", command=self.save_image, width=15)
        btn_save.pack(side=tk.LEFT, padx=10)
        
        btn_undo = tk.Button(control_frame, text="실행 취소 (Undo)", command=self.undo, width=15)
        btn_undo.pack(side=tk.LEFT, padx=10)

        # 감도(Tolerance) 조절 슬라이더
        lbl_tol = tk.Label(control_frame, text="요술봉 감도:")
        lbl_tol.pack(side=tk.LEFT, padx=(20, 5))
        
        self.tolerance_var = tk.IntVar(value=20)
        self.slider_tol = tk.Scale(control_frame, from_=0, to=150, orient=tk.HORIZONTAL, variable=self.tolerance_var, length=200)
        self.slider_tol.pack(side=tk.LEFT)

        # 캔버스 패널 (이미지 표시 및 클릭)
        self.canvas = tk.Canvas(self.root, bg="gray")
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 캔버스 이벤트 바인딩
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.root.bind("<Configure>", self.on_resize)

    def load_image(self):
        file_path = filedialog.askopenfilename(
            title="이미지 선택",
            filetypes=[("PNG Files", "*.png"), ("All Files", "*.*")]
        )
        if not file_path:
            return

        try:
            # 이미지를 불러오고 투명도 처리를 위해 RGBA 모드로 변환
            img = Image.open(file_path).convert("RGBA")
            self.original_image = img
            self.history = [] # 히스토리 초기화
            self.update_display()
        except Exception as e:
            messagebox.showerror("오류", f"이미지를 불러올 수 없습니다.\n{e}")

    def save_image(self):
        if self.original_image is None:
            messagebox.showwarning("경고", "저장할 이미지가 없습니다.")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            title="이미지 저장",
            filetypes=[("PNG Files", "*.png")]
        )
        if not file_path:
            return

        try:
            self.original_image.save(file_path, "PNG")
            messagebox.showinfo("완료", "이미지가 성공적으로 저장되었습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"이미지 저장에 실패했습니다.\n{e}")

    def undo(self):
        if self.history:
            self.original_image = self.history.pop()
            self.update_display()

    def update_display(self):
        if self.original_image is None:
            return

        self.canvas.update_idletasks()
        c_width = self.canvas.winfo_width()
        c_height = self.canvas.winfo_height()

        if c_width <= 1 or c_height <= 1:
            return

        i_width, i_height = self.original_image.size

        # 캔버스 크기에 맞게 스케일 계산 (비율 유지)
        scale_w = c_width / i_width
        scale_h = c_height / i_height
        self.scale_factor = min(scale_w, scale_h)

        new_width = int(i_width * self.scale_factor)
        new_height = int(i_height * self.scale_factor)

        # 화면에 표시될 이미지를 리사이즈
        display_img = self.original_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(display_img)

        # 캔버스 중앙 배치를 위한 오프셋
        self.offset_x = (c_width - new_width) // 2
        self.offset_y = (c_height - new_height) // 2

        self.canvas.delete("all")
        self.draw_checkerboard(c_width, c_height)
        self.canvas.create_image(self.offset_x, self.offset_y, anchor=tk.NW, image=self.tk_image)

    def draw_checkerboard(self, width, height, tile_size=15):
        # 투명한 부분을 시각적으로 확인하기 위한 체크무늬 배경
        for y in range(0, height, tile_size):
            for x in range(0, width, tile_size):
                color = "white" if ((x // tile_size) + (y // tile_size)) % 2 == 0 else "#cccccc"
                self.canvas.create_rectangle(x, y, x + tile_size, y + tile_size, fill=color, outline="")

    def on_resize(self, event):
        # 윈도우 크기 변경 시 이미지 재배치
        if event.widget == self.root:
            self.update_display()

    def on_canvas_click(self, event):
        if self.original_image is None:
            return

        # 클릭한 캔버스 좌표를 원본 이미지 좌표로 변환
        img_x = int((event.x - self.offset_x) / self.scale_factor)
        img_y = int((event.y - self.offset_y) / self.scale_factor)

        i_width, i_height = self.original_image.size

        # 이미지 영역 밖을 클릭했는지 확인
        if not (0 <= img_x < i_width and 0 <= img_y < i_height):
            return

        # Undo를 위해 현재 상태 복사 저장
        self.history.append(self.original_image.copy())
        if len(self.history) > 10:  # 메모리 관리를 위해 최근 10개까지만 저장
            self.history.pop(0)

        self.apply_magic_wand(img_x, img_y)
        self.update_display()

    def apply_magic_wand(self, x, y):
        # PIL 이미지를 Numpy 배열(OpenCV 호환)로 변환
        img_array = np.array(self.original_image)
        
        # 색상 비교를 위해 RGB 채널만 분리
        img_rgb = img_array[:, :, :3]
        
        h, w = img_rgb.shape[:2]
        
        # OpenCV floodFill을 위한 마스크 생성 (원본보다 가로세로 2픽셀씩 커야 함)
        mask = np.zeros((h + 2, w + 2), np.uint8)
        
        # 슬라이더에서 감도 값 가져오기
        tol = self.tolerance_var.get()
        tolerance = (tol, tol, tol)
        
        # 클릭한 지점을 기준으로 유사한 색상 영역 찾기 (Flood Fill)
        cv2.floodFill(
            img_rgb.copy(), 
            mask, 
            (x, y), 
            (0, 0, 0), 
            loDiff=tolerance, 
            upDiff=tolerance, 
            flags=4 | (255 << 8) | cv2.FLOODFILL_MASK_ONLY
        )
        
        # 마스크의 실제 영역 (테두리 1픽셀씩 제외)
        fill_mask = mask[1:-1, 1:-1]
        
        # 선택된 영역(mask == 255)의 Alpha 채널(투명도)을 0으로 만들어 투명하게 처리
        img_array[fill_mask == 255, 3] = 0
        
        # 변경된 배열을 다시 PIL 이미지로 변환
        self.original_image = Image.fromarray(img_array)

if __name__ == "__main__":
    root = tk.Tk()
    app = MagicWandApp(root)
    root.mainloop()