import requests
import os

def save_image_from_url(image_url, save_path):
    response = requests.get(image_url)
    if response.status_code == 200:
        with open(save_path, 'wb') as file:
            file.write(response.content)
        print(f"Image saved successfully at {save_path}")
        return save_path
    else:
        print(f"Failed to retrieve image from URL: {image_url}")


from langchain_community.utilities.dalle_image_generator import DallEAPIWrapper

def generate_image_dalle(input_prompt):
    image_url = DallEAPIWrapper(
        model="dall-e-3",
    ).run(input_prompt)
    return image_url

def generate_van_gogh_art(input_prompt):
    van_gogh_style = "in Vincent van Gogh's post-impressionist style, featuring his signature elements: dynamic swirling brushstrokes, intense colors with bold contrasts, and thick impasto technique. The color palette should emphasize deep blues, bright yellows, and rich earth tones, creating an emotionally charged atmosphere that captures the artist's distinctive vision of nature's energy and movement."
    combined_prompt = f"{input_prompt}, rendered {van_gogh_style}"
    image_url = generate_image_dalle(combined_prompt)
    return image_url
