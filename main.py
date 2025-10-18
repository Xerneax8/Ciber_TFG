import random
import subprocess
import sys

import config
from google import genai
import os
import re
from pathlib import Path
import shutil

num_versions = 10


# Get the names of the file that we are going to send to the LLM
def get_source_files(path):
    files_list = []
    if os.path.isdir(path / "controller"):
        list_per_directory = os.listdir(path / "controller")
        files_list.append([file for file in list_per_directory if "Web" in file])
    else:
        list_per_directory = os.listdir(path)
        files_list.append([file for file in list_per_directory if "Web" in file])
    return files_list


# Get an style for the frontend
def take_style():
    with open("styles.txt", "r+") as f:
        styles = f.read()
    f.close()

    styles_list = styles.split("\n")

    return styles_list[random.randint(0, len(styles_list) - 1)]

def take_theme():
    with open("themes.txt", "r+") as f:
        themes = f.read()
    f.close()

    themes_list = themes.split("\n")

    return themes_list[random.randint(0, len(themes_list) - 1)]

# Parse AI response
def parser_ai(all_text: str, directory):
    pattern = r"([\w\-/\.]+)\n```[a-zA-Z]*\n([\s\S]*?)```"

    matches = re.findall(pattern, all_text)
    if not matches:
        raise ValueError("No valid file sections found in the provided text.")

    for filepath, content in matches:
        folder = directory / filepath

        with open(folder, "w", encoding="utf-8") as f:
            f.write(content.strip() + "\n")


def call_ai(text):
    # Configuring OS to get the API Key
    os.environ['GEMINI_API_KEY'] = config.GEMINI_API_KEY

    client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))

    # Prompt to retrieve the frontend with all the parts
    return client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[{
            "role": "user",
            "parts": [{
                "text": f"Create a {take_style()} frontend for this backend with style {take_theme()}, retrieve all the necessary html, js and css"
                        f" (only use percentages here) retrieve only the code and a line above it indicating static/name"
                        f" or template/name, this last one is important, ignore the healthcheck and if a file misses. {text} "
            }]
        }]
    ).text


def check_compile(directory):
    # Check if the programs compiles
    parent_directory = os.environ.get("PWD")
    os.chdir(str(directory))

    try:
        subprocess.run(["sh", "deploy-challenge.sh"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Shell script for {str(directory)} failed with exit code {e.returncode}")
    finally:
        os.system("docker compose down")
        os.chdir(parent_directory)


# Read the code and return string
def read_code(complete_path, source_files):
    with open(complete_path / source_files[0][0], "r+") as f:
        text = f.read()
    f.close()

    return text


# Parse read code, reducing the number of tokens
def parse_code(source_code):
    lines = source_code.splitlines(keepends=True)
    result = []
    i = 0
    total_lines = len(lines)

    # Patterns to look for in each language for a web challenge
    patterns = {
        'python': re.compile(r'^\s*@app\.route'),
        'java': re.compile(r'^\s*@(?:Get|Post|Put|Delete|Request)Mapping'),
        'js': re.compile(r'^\s*app\.(get|post|put|delete)\s*\(.*')
    }

    # Detect if the code is an specific language
    def detect_language(line):
        for lang, pattern in patterns.items():
            if pattern.match(line):
                return lang
        return None

    # Check if the function is a healthcheck, do not include it
    def contain_exclude(block):
        content = ''.join(block).lower()
        return 'healthcheck' in content or 'health' in content

    while i < total_lines:
        line = lines[i]
        language = detect_language(line)

        # Java language
        if language == 'java':
            actual_block = []
            # Capture Spring Boot annotations
            while i < total_lines and lines[i].strip().startswith('@'):
                actual_block.append(lines[i])
                i += 1

            # Detect method signature
            while i < total_lines and not lines[i].strip().startswith("public"):
                actual_block.append(lines[i])
                i += 1

            # Add the signature
            if i < total_lines and lines[i].strip().startswith("public"):
                actual_block.append(lines[i])
                brace_count = lines[i].count('{') - lines[i].count('}')
                i += 1

                # Capture body function by brace balancing
                while i < total_lines and brace_count > 0:
                    actual_block.append(lines[i])
                    brace_count += lines[i].count('{') - lines[i].count('}')
                    i += 1

                if not contain_exclude(actual_block):
                    result.append(''.join(actual_block))
            continue

        # Python language
        elif language == 'python':
            actual_block = []
            indent_level = None

            # Look for def
            while i < total_lines:
                actual_block.append(lines[i])
                if lines[i].strip().startswith("def "):
                    indent_level = len(lines[i]) - len(lines[i].lstrip())
                    i += 1
                    break
                i += 1

            # Add indented body
            while i < total_lines:
                line_indent = len(lines[i]) - len(lines[i].lstrip())
                if line_indent > indent_level or not lines[i].strip():
                    actual_block.append(lines[i])
                    i += 1
                else:
                    break

            if not contain_exclude(actual_block):
                result.append(''.join(actual_block))
            continue

        # JavaScript language
        elif language == 'js':
            actual_block = [lines[i]]
            brace_count = lines[i].count('{') - lines[i].count('}')
            i += 1

            while i < total_lines and brace_count > 0:
                actual_block.append(lines[i])
                brace_count += lines[i].count('{') - lines[i].count('}')
                i += 1

            if not contain_exclude(actual_block):
                result.append(''.join(actual_block))
            continue

        else:
            i += 1

    return result


# Get all files and return the string
def generate_prompt_code(complete_path):
    source_files = get_source_files(complete_path)

    return read_code(complete_path, source_files)


def main():
    # Getting all the challenge directories of one folder
    directory = '.'
    files = os.listdir(directory)
    list_challenge_directories = sorted([file for file in files if
                                         "web" in file])  # All the directories with "web" in their name

    text = ""

    # Pipeline to get the backend file, give it to Gemini and write back the answer
    for directory in list_challenge_directories:
        dir_verions_name = directory + "-versions"
        dir_versions_complete_path  = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / dir_verions_name
        os.mkdir(dir_versions_complete_path)
        for num in range(num_versions):
            shutil.copytree(directory, dir_versions_complete_path / (directory + f"-{num+1}"))

        directory = Path(directory)

        if os.path.isdir(directory / "src" / "main" / "java"):
            complete_path_challenge_directories_java = directory / "src" / "main" / "java" / "core_files"

            text = generate_prompt_code(complete_path_challenge_directories_java)

        elif os.path.isdir(directory / "src" / "main" / "js"):
            complete_path_challenge_directories_js = directory / "src" / "main" / "js"

            text = generate_prompt_code(complete_path_challenge_directories_js)

        elif os.path.isdir(directory / "src" / "main" / "python"):
            complete_path_challenge_directories_python = directory / "src" / "main" / "python"

            text = generate_prompt_code(complete_path_challenge_directories_python)

        result = parse_code(text)

        # Create the diferents verions for the students
        for num in range(num_versions):
            parser_ai(call_ai(result), dir_versions_complete_path / (str(directory) + f"-{num+1}") / "src" / "main" / "resources")

        check_compile(directory)


if __name__ == "__main__":
    main()
