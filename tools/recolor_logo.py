from PIL import Image, ImageDraw
import numpy as np

def apply_gradient(input_path, output_path, start_color, end_color):
    """
    Applies a linear gradient to the non-background parts of an image.
    Assumes background is uniform (top-left pixel).
    """
    try:
        img = Image.open(input_path).convert("RGBA")
        width, height = img.size
        
        # Get background color from top-left pixel
        bg_color = img.getpixel((0, 0))
        
        # Create a mask for the logo (anything not background)
        # Using a distance threshold for background removal to handle JPEG artifacts
        datas = img.getdata()
        new_data = []
        
        # Simple threshold
        tolerance = 30
        
        # Create gradient image
        gradient = Image.new('RGBA', (width, height), color=0)
        draw = ImageDraw.Draw(gradient)
        
        # Linear interpolation for gradient
        for y in range(height):
            # Calculate color for this row (vertical gradient for simplicity, or diagonal)
            # Let's do diagonal from top-left to bottom-right
            # But iterating pixels is slow in python.
            # Let's just do a vertical gradient which is faster to draw with lines
             pass

        # Create diagonal gradient array
        # Create meshgrid for coordinates
        x = np.linspace(0, 1, width)
        y = np.linspace(0, 1, height)
        X, Y = np.meshgrid(x, y)
        
        # Calculate weights for diagonal gradient (top-left to bottom-right)
        weights = (X + Y) / 2
        
        # Parse hex colors
        c1 = tuple(int(start_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
        c2 = tuple(int(end_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
        
        # Generate gradient array
        r = (c1[0] * (1 - weights) + c2[0] * weights).astype(np.uint8)
        g = (c1[1] * (1 - weights) + c2[1] * weights).astype(np.uint8)
        b = (c1[2] * (1 - weights) + c2[2] * weights).astype(np.uint8)
        
        # Stack to RGBA
        gradient_arr = np.dstack((r, g, b, np.full((height, width), 255, dtype=np.uint8)))
        gradient_img = Image.fromarray(gradient_arr, 'RGBA')

        # Create mask from original image
        # Convert to grayscale and invert to get the shape?
        # If it's a green logo on white background:
        # Green is dark, White is light.
        # In grayscale: White=255, Green=~128.
        # We want White to be transparent (alpha 0) and Green to be opaque (alpha 255).
        # But we want to preserve the shape.
        
        # Better approach: Transparency based on difference from background
        img_arr = np.array(img)
        bg_arr = np.array(bg_color)
        
        # Calculate distance from background color
        diff = np.linalg.norm(img_arr[:,:,:3] - bg_arr[:3], axis=2)
        
        # Create alpha channel: 0 where close to bg, 255 where far
        # Smooth transition/anti-aliasing:
        # If diff > tolerance, alpha = 255. Else alpha = scaled diff?
        # Let's just use strict threshold for now, or use the grayscale as alpha
        
        # Assuming white background, the darkness of the pixel is the alpha
        # (Darker = more opaque)
        gray = img.convert('L')
        gray_arr = np.array(gray)
        
        # Invert grayscale: White(255) -> 0, Black(0) -> 255
        alpha = 255 - gray_arr
        
        # Create new image
        # Use the gradient as base, apply the calculated alpha
        result = gradient_img.copy()
        result.putalpha(Image.fromarray(alpha.astype(np.uint8)))
        
        # But wait, if the original logo had shading, we lose it if we just replace with flat gradient.
        # If the user wants "same picture... but gradient", they probably want to keep the shape and apply the gradient as the fill.
        # If the original had internal details (shading), this method flattens it.
        # Given "avatar", it's likely a flat icon.
        
        result.save(output_path)
        print(f"Gradient applied and saved to {output_path}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    apply_gradient(
        '/Users/sedatif2/.gemini/antigravity/scratch/wat-agent-template/output/logo.png',
        '/Users/sedatif2/.gemini/antigravity/scratch/wat-agent-template/output/slipsync_logo_line_green_v2.png',
        '#06C755',
        '#00B900'
    )
