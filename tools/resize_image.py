from PIL import Image
import sys
import os

def resize_image(image_path):
    try:
        img = Image.open(image_path)
        print(f"Original size: {img.size}")
        
        target_width = 640
        target_height = 360
        target_ratio = target_width / target_height
        
        orig_width, orig_height = img.size
        orig_ratio = orig_width / orig_height
        
        if orig_ratio > target_ratio:
            # Original is wider than 16:9, crop sides
            new_width = int(orig_height * target_ratio)
            left = (orig_width - new_width) / 2
            img = img.crop((left, 0, left + new_width, orig_height))
        else:
            # Original is taller than 16:9 (or same), crop top/bottom
            new_height = int(orig_width / target_ratio)
            top = (orig_height - new_height) / 2
            img = img.crop((0, top, orig_width, top + new_height))
            
        # Now resize to exactly 640x360
        new_img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
        
        new_img.save(image_path)
        print(f"Professionally resized (cropped & scaled) image saved to {image_path}")
        print(f"New size: {new_img.size}")
    except Exception as e:
        print(f"Error resizing image: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 resize_image.py <image_path>")
        sys.exit(1)
    
    image_path = sys.argv[1]
    resize_image(image_path)
