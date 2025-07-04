# 보강 데이터 수집 포함

import os
import cv2
import numpy as np
import mediapipe as mp
from collections import deque
from tensorflow.keras.models import load_model
from PIL import Image, ImageFont, ImageDraw

# ==== 설정 ====
SEQ_NAME = "L20"  # ← 여기를 바꾸면 시퀀스 테스트 가능: 'L10', 'L20', ...
CONF_THRESH = 0.2
CURRENT_LABEL = "좋다"

# ==== 경로 설정 ====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(BASE_DIR, '../..'))  # Sign2Text/
MODEL_DIR = os.path.join(PROJECT_DIR, 'models', SEQ_NAME)
DATASET_DIR = os.path.join(PROJECT_DIR, 'dataset/augmented_samples', CURRENT_LABEL)

# ==== 모델 및 메타데이터 로드 ====
model = load_model(os.path.join(MODEL_DIR, 'sign_language_model_normalized.h5'))
label_classes = np.load(os.path.join(MODEL_DIR, 'label_classes.npy'), allow_pickle=True)
X_mean = np.load(os.path.join(MODEL_DIR, 'X_mean.npy'))
X_std = np.load(os.path.join(MODEL_DIR, 'X_std.npy'))
id2label = {i: lbl for i, lbl in enumerate(label_classes)}

WINDOW_SIZE = int(SEQ_NAME[1:])  # 'L10' → 10

# ==== MediaPipe 설정 ====
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(static_image_mode=False, max_num_hands=2, min_detection_confidence=0.7, min_tracking_confidence=0.7)
mp_drawing = mp.solutions.drawing_utils

# ==== 시퀀스 버퍼 ====
sequence = deque()
collecting = False
latest_text = ""

# ==== 폰트 ====
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/AppleGothic.ttf", 32)

def draw_text(img, text, pos=(10,50), color=(255,255,0)):
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)
    draw.text(pos, text, font=font, fill=color)
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

def extract_rel(lms, W, H):
    if not lms:
        return [0]*42
    pts = [(p.x*W, p.y*H) for p in lms]
    bx, by = pts[0]
    rel = []
    for x, y in pts:
        rel += [x-bx, y-by]
    return rel

def calc_ang(lms):
    if not lms:
        return [0]*15
    ang = []
    for i in range(len(lms)-2):
        a = np.array([lms[i].x, lms[i].y])
        b = np.array([lms[i+1].x, lms[i+1].y])
        c = np.array([lms[i+2].x, lms[i+2].y])
        ba = a - b
        bc = c - b
        cos = np.dot(ba, bc) / (np.linalg.norm(ba)*np.linalg.norm(bc)+1e-6)
        ang.append(np.degrees(np.arccos(np.clip(cos, -1, 1))))
    return ang[:15] + [0]*(15 - len(ang))

# ==== 웹캠 열기 ====
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
if not cap.isOpened():
    cap.release()
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ 웹캠 열기 실패")
        exit(1)

cv2.namedWindow("Sign2Text 실시간 수어 인식", cv2.WINDOW_NORMAL)
print("s=수집 시작/중지, p=예측, w=저장, q=종료")

while True:
    ret, frame = cap.read()
    if not ret:
        continue
    img = cv2.flip(frame, 1)
    H, W = img.shape[:2]
    results = hands.process(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

    left, right = [], []
    if results.multi_hand_landmarks:
        for lm, hd in zip(results.multi_hand_landmarks, results.multi_handedness):
            if hd.classification[0].label == 'Left':
                left = lm.landmark
            else:
                right = lm.landmark
            mp_drawing.draw_landmarks(img, lm, mp_hands.HAND_CONNECTIONS)

    feats = (
        extract_rel(left, W, H) +
        extract_rel(right, W, H) +
        calc_ang(left) +
        calc_ang(right)
    )
    if collecting and sum(abs(f) for f in feats) != 0:
        sequence.append(feats)

    status = f"{'수집 중' if collecting else '대기 중'} | seq_len={len(sequence)}"
    img = draw_text(img, status)
    if latest_text:
        img = draw_text(img, f"결과: {latest_text}", pos=(10, 100))

    cv2.imshow("Sign2Text 실시간 수어 인식", img)

    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break

    elif key == ord('s'):
        collecting = not collecting
        if collecting:
            sequence.clear()
            latest_text = ""
            print("🔘 수집 시작")
        else:
            print("🔘 수집 중지")

    elif key == ord('p'):
        if len(sequence) >= WINDOW_SIZE:
            seq_arr = np.array(sequence)
            n_windows = len(seq_arr) - WINDOW_SIZE + 1
            windows = np.stack([
                seq_arr[i:i+WINDOW_SIZE] for i in range(n_windows)
            ], axis=0)
            normed = (windows - X_mean) / X_std
            preds = model.predict(normed, verbose=0)
            win_idx = preds.max(axis=1).argmax()
            best_pred = preds[win_idx]
            class_idx = best_pred.argmax()
            best_conf = best_pred[class_idx]
            if best_conf > CONF_THRESH:
                latest_text = f"{id2label[class_idx]}"
                print("🔍 예측 결과:", latest_text)
            else:
                print(f"❗ 신뢰도 부족: {best_conf:.2f}")
            sequence.clear()
        else:
            print(f"❗ 시퀀스 길이 부족: {len(sequence)}/{WINDOW_SIZE}")

    elif key == ord('w'):
        if len(sequence) >= WINDOW_SIZE:
            os.makedirs(DATASET_DIR, exist_ok=True)
            count = len([f for f in os.listdir(DATASET_DIR) if f.endswith(".npy")]) // 2 + 1
            filename_raw = f"raw_seq_{CURRENT_LABEL}_{count}.npy"
            filename_norm = f"norm_seq_{CURRENT_LABEL}_{count}.npy"
            seq_arr = np.array(sequence)[-WINDOW_SIZE:]
            np.save(os.path.join(DATASET_DIR, filename_raw), seq_arr)
            np.save(os.path.join(DATASET_DIR, filename_norm), (seq_arr - X_mean) / X_std)
            print(f"✅ 저장 완료: {filename_raw}, {filename_norm}")
        else:
            print(f"❗ 시퀀스 길이 부족하여 저장 불가: {len(sequence)}/{WINDOW_SIZE}")

cap.release()
cv2.destroyAllWindows()