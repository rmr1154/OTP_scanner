from PIL import Image
import pytesseract             # Python interface to tesseract for OCR
import cv2                     # OpenCV computer vision library
import os
import numpy as np
import re
import subprocess
import pyautogui               # GUI automation library
import time
import sys
import pwd
import credentials             # Config file containing username, password, ping URL.
                               # Create credentials.py in the same directory containing
                               # login = {'username' : '...', 'password' : '...', 'url' : '...'}


pyautogui.PAUSE = 0.5          # Pause one second after each pyautogui command
pyautogui.FAILSAFE = True      # Moving cursor to top-left will cause exception
DEBUG = False                  # If True, write intermediate images to /tmp


# Get image from webcam using OpenCV
def get_image():
    print("Waiting 5 seconds before capturing image... Press <space> to capture image immediately...")

    cam = cv2.VideoCapture(0)  # ls /sys/class/video4linux

    # Hardware defaults for Lenovo T400s
    cam.set(3, 1280)           # Width
    cam.set(4, 720)            # Height
    cam.set(10, 128/255)       # Brightness (max = 255)
    cam.set(11, 32/255)        # Contrast (max = 255)
    cam.set(12, 64/100)        # Saturation (max = 100)
    cam.set(13, 0.5)           # Hue (0 = -180, 1 = +180)

    num_frames = 0
    while True:
        ret, image = cam.read()
        if not ret:
            print("Camera not functional...")
            sys.exit(1)

        cv2.imshow('image', image)

        # Capture image if <space> is pressed
        if (cv2.waitKey(1) & 0xFF) == ord(' '):
            break

        # Wait 5 seconds before capturing image
        num_frames += 1
        if num_frames / 10 == 5:
            break

    cam.release()
    cv2.destroyAllWindows()

    if DEBUG:
        cv2.imwrite("/tmp/image.jpg", image)

    return image


# Key for sorting contours based on the area of their bounding box
def contour_key(contour):
    # Get the minimum area bounding box for the contour
    rect = cv2.minAreaRect(contour)

    # Get the width and height of the bounding box
    w, h = rect[1]
    if w < h:
        w, h = h, w

    # We are only interested in rectangular contours (w > h)
    if h > 0 and w/h < 2:
        return 0
    else:
        return w * h


# Get contours
def get_contours(image):
    # Remove noise with median blurring
    image = cv2.medianBlur(image, 5)

    # Convert to grayscale
    grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Run canny edge detector on the grayscale image
    edge = cv2.Canny(grayscale, 100, 200)

    # Dilate the edge image to get a blob of text
    kernel = np.ones((1,5), dtype=np.uint8)
    dilated = cv2.dilate(edge, kernel, iterations = 10)

    if DEBUG:
        cv2.imwrite("/tmp/edge.jpg", edge)
        cv2.imwrite("/tmp/dilated.jpg", dilated)

    # Find the top 10 contours based on the area of their bounding rectangles
    contours = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)[1]
    contours = sorted(contours, key=contour_key, reverse=True)[:10]

    return contours


# Get minimum area bounding boxes for the contours
def get_bounding_boxes(contours, image):
    boxes = []

    if DEBUG:
        image_copy = image.copy()

    for contour in contours:
        rect = cv2.minAreaRect(contour)
        boxes.append(rect)

        # Overlay bounding box on top of the webcam image
        if DEBUG:
            box = cv2.boxPoints(rect)
            box = np.int0(box)
            cv2.drawContours(image_copy, [box], 0, (0,255,0), 3)

    if DEBUG:
        cv2.imwrite("/tmp/boxes.jpg", image_copy)

    return boxes


# Perform OCR on a single box
def ocr_int(i, box, image):
    center = box[0] # Center of the bounding rectangle
    w, h   = box[1] # Width and height of the bounding rectangle
    angle  = box[2] # Angle of the bounding rectangle
    if w < h:
        w, h = h, w
        angle += 90.0

    rows, cols, _ = image.shape

    # Rotate image
    M       = cv2.getRotationMatrix2D(center, angle, 1)
    rotated = cv2.warpAffine(image, M, (cols, rows))

    # Crop rotated image.
    # Ensure that the crop region lies within the image.
    start_x = int(center[1] - (h / 2))
    end_x   = int(start_x + h)
    start_y = int(center[0] - (w / 2))
    end_y   = int(start_y + w)
    start_x = start_x if 0 <= start_x < rows else (0 if start_x < 0 else rows-1)
    end_x   = end_x if 0 <= end_x < rows else (0 if end_x < 0 else rows-1)
    start_y = start_y if 0 <= start_y < cols else (0 if start_y < 0 else cols-1)
    end_y   = end_y if 0 <= end_y < cols else (0 if end_y < 0 else cols-1)
    crop    = rotated[start_x:end_x, start_y:end_y]

    # Convert to grayscale
    grayscale = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Convert to a binary image using adaptive thresholding.
    # High value for the 5th parameter = Thick digits.
    # High value for the 6th parameter = Reduce noise.
    threshold = cv2.adaptiveThreshold(grayscale, 255, \
                                      cv2.ADAPTIVE_THRESH_GAUSSIAN_C, \
                                      cv2.THRESH_BINARY, 1001, 11)

    if DEBUG:
        cv2.imwrite("/tmp/threshold_crop_" + str(i) + ".jpg", threshold)

    text = pytesseract.image_to_string(Image.fromarray(threshold, "L"), \
                                       config="-psm 7 -c tessedit_char_whitelist=1234567890")

    return text


# Perform OCR on all the bounding boxes
def ocr(boxes, image):
    for i, box in enumerate(boxes):
        try:
            text = ocr_int(i, box, image)
        except KeyboardInterrupt:
            raise
        except:
            text = None

        if text:
            r = re.search(r'(\d{3})[ ]?(\d{3})', text)
            if r:
                return (r.group(1) + r.group(2))

    return None


# Wait until the given image appears on the screen before taking the next step
def pyautogui_wait(image):
    w, h = pyautogui.size()

    while True:
        center = pyautogui.locateCenterOnScreen(image, region=(400, 100, w-400, h-100))
        if center:
            return center
        else:
            time.sleep(0.5)


PYAUTOGUI_IMAGES_PATH = os.path.join(os.path.dirname(sys.argv[0]), 'pyautogui_images')


# GUI automation using pyautogui to connect to VPN if not already connected
def connect_VPN_CISCO():
    ping_cmd = 'ping -c 1 ' + credentials.login['url'] + ' > /dev/null 2>&1'

    resp = os.system(ping_cmd)
    if resp == 0:
        # Already connect to VPN
        print("Already connected to VPN...")
        return False

    # Start cisco anyconnect as normal user
    uid = pwd.getpwnam('varun')[2]
    os.setuid(uid)
    subprocess.Popen(["/opt/cisco/anyconnect/bin/vpnui"], \
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


    pyautogui_wait(os.path.join(PYAUTOGUI_IMAGES_PATH, 'vpn_cisco1.png'))
    pyautogui.typewrite(credentials.login['password'])
    pyautogui.press('tab')
    pyautogui.typewrite(text) # OTP
    pyautogui.press('enter')
    pyautogui_wait(os.path.join(PYAUTOGUI_IMAGES_PATH, 'vpn_cisco2.png'))
    pyautogui.press('enter')

    # Wait until VPN connection is successful
    while True:
        resp = os.system(ping_cmd)
        if resp == 0:
            print("Connected to VPN...")
            break
        time.sleep(0.5)

    return True


def connect_VPN():
    ping_cmd = 'ping -c 1 ' + credentials.login['url'] + ' > /dev/null 2>&1'

    resp = os.system(ping_cmd)
    if resp == 0:
        # Already connect to VPN
        print("Already connected to VPN...")
        return False

    cols, rows = pyautogui.size()

    pyautogui.moveTo(cols-2, rows-2)
    pyautogui.click()
    time.sleep(1)
    pyautogui.moveTo(1447, 681)
    pyautogui.click()
    pyautogui_wait(os.path.join(PYAUTOGUI_IMAGES_PATH, 'vpn.png'))
    pyautogui.typewrite(credentials.login['password'])
    pyautogui.press('tab')
    pyautogui.typewrite(text) # OTP
    pyautogui.press('enter')

    # Wait until VPN connection is successful
    while True:
        resp = os.system(ping_cmd)
        if resp == 0:
            print("Connected to VPN...")
            break
        time.sleep(0.5)

    return True


# GUI automation using pyautogui to start the virtual desktop if not already running
def start_virtual_desktop():
    ps = subprocess.Popen(['ps', 'ax'], stdout=subprocess.PIPE)
    out = ps.communicate()[0]
    for line in out.decode('utf-8').split('\n'):
        if 'vmware-view' in line:
            # Nothing to do if the virtual desktop is already running
            print("Virtual desktop already running...")
            return False

    subprocess.Popen(["vmware-view"], \
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    center = pyautogui_wait(os.path.join(PYAUTOGUI_IMAGES_PATH, 'vd1.png'))
    pyautogui.moveTo(*center)
    pyautogui.doubleClick()
    pyautogui_wait(os.path.join(PYAUTOGUI_IMAGES_PATH, 'vd2.png'))
    pyautogui.typewrite(credentials.login['username'])
    pyautogui.press('tab')
    pyautogui.typewrite(text) # OTP
    pyautogui.press('enter')
    pyautogui_wait(os.path.join(PYAUTOGUI_IMAGES_PATH, 'vd3.png'))
    pyautogui.typewrite(credentials.login['password'])
    pyautogui.press('enter')
    center = pyautogui_wait(os.path.join(PYAUTOGUI_IMAGES_PATH, 'vd4.png'))
    pyautogui.moveTo(*center)
    pyautogui.doubleClick()
    print("Started virtual desktop...")

    return True


while True:
    try:
        # Get image from webcam
        image = get_image()

        # Get contours from image
        contours = get_contours(image)

        # Get bounding boxes for the contours
        boxes = get_bounding_boxes(contours, image)

        # Perform OCR
        text = ocr(boxes, image)
        if text:
            print("Success:", text)

            # Copy text to clipboard
            os.system('echo "%s" | xsel -i' % text)
        else:
            print("Failed... Try again...")
            continue

        # Connect to VPN if not already connected
        if connect_VPN_CISCO():
            continue

        # Start the virtual desktop if not already running
        start_virtual_desktop()
        break

    except KeyboardInterrupt:
        sys.exit(0)
