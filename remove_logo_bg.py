from PIL import Image
import numpy as np
import os

try:
    src = r"C:\Users\dines\.gemini\antigravity\brain\8d54afe1-df8e-44fe-9e4b-f42d25969a92\abstract_symbol_logo_1764969767412.png"
    dst = r"c:\Users\dines\Music\mixture\intelligent-compiler-studio\public\logo.png"

    print(f"Processing {src}...")
    img = Image.open(src).convert("RGBA")
    data = np.array(img)

    # Decompose channels
    red, green, blue, alpha = data.T

    # Define threshold for "black"
    # We want to be careful not to remove dark parts of the logo itself if they are important, 
    # but usually pure black background removal uses a low threshold.
    threshold = 30
    black_areas = (red < threshold) & (green < threshold) & (blue < threshold)

    # Set alpha to 0 for black areas
    data[..., 3][black_areas.T] = 0

    img_new = Image.fromarray(data)
    img_new.save(dst)
    print(f"Saved transparent logo to {dst}")

except Exception as e:
    print(f"Error: {e}")
