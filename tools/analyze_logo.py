from PIL import Image
import numpy as np

img_path = '/Users/sedatif2/.gemini/antigravity/scratch/wat-agent-template/output/logo.png'
try:
    img = Image.open(img_path)
    print(f"Format: {img.format}")
    print(f"Mode: {img.mode}")
    print(f"Size: {img.size}")
    
    # Check for transparency
    if img.mode == 'RGBA':
        extrema = img.getextrema()
        print(f"Alpha channel extrema: {extrema[3]}")
    
    # Sample some center pixels
    mid_x, mid_y = img.size[0] // 2, img.size[1] // 2
    pixel = img.getpixel((mid_x, mid_y))
    print(f"Center pixel: {pixel}")

except Exception as e:
    print(f"Error: {e}")
