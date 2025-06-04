import cv2
import numpy as np
import sys
import os
import serial
import requests
sys.path.append('C:\\Users\\datnc\\Documents\\project\\yolov5')
from models.common import DetectMultiBackend
from utils.general import non_max_suppression, scale_boxes
from utils.plots import Annotator, colors
import pytesseract
from flask import Flask, Response, render_template
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
import time
import os
from datetime import datetime

# Cấu hình
URL_STREAM = "http://172.16.1.98/"  # IP của ESP32-CAM
PIXEL_TO_METER = 0.05  # Hiệu chỉnh: 100 pixel = 5 mét
SPEED_LIMIT = 50  # Giới hạn tốc độ (km/h)
FPS = 25  # FPS trung bình

# Khởi tạo YOLOv5
model = DetectMultiBackend('C:\\Users\\datnc\\Documents\\project\\yolov5\\yolov5s.pt', device='cpu')
model.eval()

# Khởi tạo Flask
app = Flask(__name__)

# Khởi tạo video capture với requests
def init_video_capture(url):
    print(f"Thử mở luồng video tại {url} bằng OpenCV VideoCapture")
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        print("Không thể mở luồng video")
        exit()
    return cap

def read_frame(cap):
    ret, frame = cap.read()
    return ret, frame
# Khởi tạo tracker
def create_tracker():
    if hasattr(cv2, 'TrackerKCF_create'):
        return cv2.TrackerKCF_create()
    elif hasattr(cv2, 'legacy') and hasattr(cv2.legacy, 'TrackerKCF_create'):
        return cv2.legacy.TrackerKCF_create()
    else:
        raise Exception("Không tìm thấy TrackerKCF_create trong cv2")

tracker = create_tracker()

cap = init_video_capture(URL_STREAM)
ret, frame = read_frame(cap)

if ret:
    results = model(frame)
    detections = non_max_suppression(results)[0]
    if detections is not None and len(detections) > 0:
        for *xyxy, conf, cls in detections:
            if cls in [2, 3, 5, 7]:  # Ô tô, xe máy, xe tải
                bbox = [int(x) for x in xyxy]
                tracker.init(frame, tuple(bbox))
                break

# Cấu hình email
EMAIL_ADDRESS = "datncpro03@gmail.com"
EMAIL_PASSWORD = "datncpro2610"  # App Password từ Google
RECIPIENT_EMAIL = "datncpro03@gmail.com"

# Kết nối với Arduino UNO
try:
    ser = serial.Serial('COM3', 9600)
    time.sleep(2)  # Chờ Arduino khởi động
    print("Kết nối với Arduino UNO thành công!")
except Exception as e:
    print(f"Không thể kết nối với Arduino UNO: {e}")
    ser = None

def preprocess_plate_image(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh

def detect_plate(image):
    if image.shape[0] == 0 or image.shape[1] == 0:
        return "Không nhận diện được biển số"
    height, width = image.shape[:2]
    plate_region = image[int(height*0.7):height, 0:width]
    return pytesseract.image_to_string(preprocess_plate_image(plate_region), config='--psm 6')

def send_email(speed, timestamp, plate, image_path):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = f"Vi phạm tốc độ - {timestamp}"

    body = f"Tốc độ: {speed:.1f} km/h\nThời gian: {timestamp}\nBiển số: {plate}"
    msg.attach(MIMEText(body, 'plain'))

    with open(image_path, 'rb') as f:
        img = MIMEImage(f.read(), name=os.path.basename(image_path))
        msg.attach(img)

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        print(f"Email gửi thành công cho vi phạm lúc {timestamp}")
    except Exception as e:
        print(f"Lỗi gửi email: {e}")

def gen_frames():
    prev_center = None
    prev_time = time.time()
    violation_count = 0
    while True:
        ret, frame = read_mjpeg_stream(stream)
        if not ret:
            print("Lỗi đọc frame, thử lại sau 1s...")
            time.sleep(1)
            continue

        # Phát hiện và theo dõi
        results = model(frame)
        detections = non_max_suppression(results)[0]
        annotator = Annotator(frame, line_width=2, pil=False)
        if detections is not None and len(detections) > 0:
            for *xyxy, conf, cls in detections:
                if cls in [2, 3, 5, 7] and conf > 0.5:  # Ô tô, xe máy, xe tải
                    bbox = [int(x) for x in scale_boxes(frame.shape[2:], xyxy, frame.shape).tolist()]
                    x1, y1, x2, y2 = bbox
                    center = ((x1 + x2) // 2, (y1 + y2) // 2)

                    if prev_center:
                        dx = center[0] - prev_center[0]
                        dy = center[1] - prev_center[1]
                        distance_pixel = np.sqrt(dx**2 + dy**2)
                        distance_meter = distance_pixel * PIXEL_TO_METER
                        current_time = time.time()
                        delta_t = current_time - prev_time
                        if delta_t > 0:
                            speed = (distance_meter / delta_t) * 3.6  # km/h

                            # Vẽ bounding box và tốc độ
                            label = f"Speed: {speed:.1f} km/h"
                            annotator.box_label(bbox, label, color=colors(0, True), txt_color=(255, 255, 255))

                            # Phát hiện vi phạm
                            if speed > SPEED_LIMIT:
                                violation_count += 1
                                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                violation_image = f"violations/violation_{violation_count}_{timestamp}.jpg"
                                cv2.imwrite(violation_image, frame)
                                plate = detect_plate(frame[y1:y2, x1:x2])
                                send_email(speed, timestamp, plate, violation_image)
                                print(f"Vi phạm #{violation_count}: Tốc độ {speed:.1f} km/h, Biển số {plate}")
                                if ser:
                                    ser.write(b'V')  # Gửi tín hiệu đến Arduino

                    prev_center = center
                    prev_time = current_time
        else:
            prev_center = None

        frame = annotator.result()
        ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=6')
if __name__ == "__main__":
    if not os.path.exists("violations"):
        os.makedirs("violations")
    print("Hệ thống khởi động, truy cập http://<IP>:5000")
    app.run(host='0.0.0.0', port=8000, threaded=True, debug=True)