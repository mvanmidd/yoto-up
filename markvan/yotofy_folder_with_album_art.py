#!/usr/bin/env python3
"""
Extract album art from MP3 files and save as PNG.
"""
import argparse
import re
from pathlib import Path

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC
from PIL import Image
import io
import sys

from yoto_up.yoto import get_api
from yoto_up.yoto_api import YotoAPI

# CARD_ID = "c7wQU" # Teddy Jams
CARD_ID = "3db3h" # Teddy Jams 2.0


def extract_album_art(mp3_path, output_path="album_art.png"):
    """
    Extract album art from an MP3 file and save it as a PNG.
    
    Args:
        mp3_path: Path to the MP3 file
        output_path: Path where the PNG should be saved
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Load the MP3 file
        audio = MP3(mp3_path, ID3=ID3)

        # Look for album art (APIC frames)
        for tag in audio.tags.values():
            if isinstance(tag, APIC):
                # APIC contains the album art
                print(f"Found album art: {tag.mime} ({len(tag.data)} bytes)")
                print(f"Description: {tag.desc}")
                print(f"Type: {tag.type}")

                # Load the image data
                image = Image.open(io.BytesIO(tag.data))
                print(f"Image size: {image.size[0]}x{image.size[1]}")
                print(f"Image format: {image.format}")

                # Convert to RGB if necessary (in case it's RGBA or other format)
                if image.mode in ('RGBA', 'LA', 'P'):
                    # Create a white background
                    background = Image.new('RGB', image.size, (255, 255, 255))
                    if image.mode == 'P':
                        image = image.convert('RGBA')
                    background.paste(image, mask=image.split()[-1] if image.mode in ('RGBA', 'LA') else None)
                    image = background
                elif image.mode != 'RGB':
                    image = image.convert('RGB')

                # Save as PNG
                image.save(output_path, 'PNG')
                print(f"Album art saved to {output_path}")
                return True

        print("No album art found in file")
        return False

    except Exception as e:
        print(f"Error extracting album art: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Rename MP3 files with numbered prefix based on modification time'
    )
    parser.add_argument('--folder', help='Folder containing MP3 files', required=False,
                        default="/Users/mvanmidd/src/yoto-up/fake_album")
    parser.add_argument('--img-folder', help='Folder containing MP3 files', required=False,
                        default=None)
    parser.add_argument(
        '-x', '--execute',
        action='store_true',
        help='Execute the rename operations (default is dry run) UNUSED'
    )

    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        print(f"Error: Folder '{folder}' does not exist")
        sys.exit(1)

    if not folder.is_dir():
        print(f"Error: '{folder}' is not a directory")
        sys.exit(1)

    # Find all MP3 files
    mp3_files = list(folder.glob("*.mp3"))

    if not mp3_files:
        print(f"No MP3 files found in '{folder}'")
        return

    api = YotoAPI(client_id="RslORm04nKbhf04qb91r2Pxwjsn3Hnd5")

    # Sort directory alphabetically
    mp3_files.sort()

    for file_path in mp3_files:
        if not args.img_folder:
            # Extract album art from ID3 metadata and write as png
            track_image_file = file_path.parent / (file_path.name + ".png")
            print(f"Extracting album art from: {file_path}")
            extract_album_art(file_path, track_image_file)
        else:
            pattern = r"^\d+\s*-\s*(?:\d+\s*-\s*)?(.+?)\.mp3$"
            match = re.search(pattern, file_path.name)
            if match:
                songname = match.group(1).strip()
                # TODO temporary hack for my current folder of images
                track_image_file = Path(args.img_folder) / (f"cleaned - {songname} 0.png")
                if not Path(track_image_file).exists():
                    raise ValueError(f"{track_image_file} not found")
                print(f"Adding song: {songname}")
            else:
                raise ValueError(f"Did not find matching image file for {file_path.name}")

        # Upload album art
        response = api.upload_custom_icon(track_image_file)
        icon_id = response['mediaId']
        # icon_id = 'J2Q6Jc2iIM63aPcjdU-dp5lMzcsyXESERYz3R31guVg'
        icon_uri = f"yoto:#{icon_id}"

        # Upload the audio
        response = api.upload_audio_to_existing_card(file_path, card_id=CARD_ID, icon_uri=icon_uri)
        print(response)


if __name__ == "__main__":
    main()
