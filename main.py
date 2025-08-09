import random
import config
from google import genai
import os
import re
from pathlib import Path

num_versions = 0


def get_source_files(path):
    files_list = []
    if os.path.isdir(path / "controller"):
        list_per_directory = os.listdir(path / "controller")
        files_list.append([file for file in list_per_directory if "Web" in file])
    else:
        list_per_directory = os.listdir(path)
        files_list.append([file for file in list_per_directory if "Web" in file])
    return files_list


def take_style():
    with open("styles.txt", "r+") as f:
        styles = f.read()
    f.close()

    styles_list = styles.split("\n")

    return styles_list[random.randint(0, len(styles_list) - 1)]


# Parse AI response

def parser_ai(all_text: str, directory):
    """
    Parses a multi-file text block and writes each file to the correct folder
    (supports static/ and templates/ or any other folder in the path).
    """

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
        contents=[f"Create a {take_style()} frontend for this backend, retrieve all the necessary html, js and css ("
                  "only use percentages here) retrieve only the code "
                  "and a line above it indicating static/name or template/name, ignore the healthcheck and if "
                  "a file misses.",
                  text]
    ).text


def check_compile(directory):
    # Check if the challenge compiles
    os.system("sh " + directory + "/deploy-challenge.sh")


def read_code(complete_path, source_files):
    with open(complete_path / source_files[0][0], "r+") as f:
        text = f.read()
    f.close()

    return text


def parse_code(source_code):
    lines = source_code.splitlines(keepends=True)
    result = []
    i = 0
    total_lines = len(lines)

    patterns = {
        'python': re.compile(r'^\s*@app\.route'),
        'java': re.compile(r'^\s*@(?:Get|Post|Put|Delete|Request)Mapping'),
        'js': re.compile(r'^\s*app\.(get|post|put|delete)\s*\(.*')
    }

    def detect_type(line):
        for lang, pattern in patterns.items():
            if pattern.match(line):
                return lang
        return None

    def contiene_excluidos(bloque):
        contenido = ''.join(bloque).lower()
        return 'healthcheck' in contenido or 'health' in contenido

    while i < total_lines:
        line = lines[i]
        tipo = detect_type(line)

        if tipo == 'java':
            actual_block = []
            # Captura todas las anotaciones arriba del método
            while i < total_lines and lines[i].strip().startswith('@'):
                actual_block.append(lines[i])
                i += 1

            # Ahora debe venir la firma del método
            while i < total_lines and not lines[i].strip().startswith("public"):
                actual_block.append(lines[i])
                i += 1

            # Agrega la firma del método
            if i < total_lines and lines[i].strip().startswith("public"):
                actual_block.append(lines[i])
                brace_count = lines[i].count('{') - lines[i].count('}')
                i += 1

                # Captura del cuerpo por balanceo de llaves
                while i < total_lines and brace_count > 0:
                    actual_block.append(lines[i])
                    brace_count += lines[i].count('{') - lines[i].count('}')
                    i += 1

                if not contiene_excluidos(actual_block):
                    result.append(''.join(actual_block))
            continue

        elif tipo == 'python':
            actual_block = []
            indent_level = None

            # Agrega decoradores y busca `def`
            while i < total_lines:
                actual_block.append(lines[i])
                if lines[i].strip().startswith("def "):
                    indent_level = len(lines[i]) - len(lines[i].lstrip())
                    i += 1
                    break
                i += 1

            # Agrega el cuerpo indentado
            while i < total_lines:
                line_indent = len(lines[i]) - len(lines[i].lstrip())
                if line_indent > indent_level or not lines[i].strip():
                    actual_block.append(lines[i])
                    i += 1
                else:
                    break

            if not contiene_excluidos(actual_block):
                result.append(''.join(actual_block))
            continue

        elif tipo == 'js':
            actual_block = [lines[i]]
            brace_count = lines[i].count('{') - lines[i].count('}')
            i += 1

            while i < total_lines and brace_count > 0:
                actual_block.append(lines[i])
                brace_count += lines[i].count('{') - lines[i].count('}')
                i += 1

            if not contiene_excluidos(actual_block):
                result.append(''.join(actual_block))
            continue

        else:
            i += 1

    return result


def generate_prompt_code(complete_path):
    source_files = get_source_files(complete_path)

    return read_code(complete_path, source_files)


def main():
    # Getting all the challenge directories of one folder
    directory = '.'
    files = os.listdir(directory)
    list_challenge_directories = [file for file in files if
                                  "web" in file]  # All the directories with "web" in their name

    text = ""
    # Pipeline to get the backend file, give it to Gemini and write back the answer

    for directory in list_challenge_directories:
        complete_path_challenge_directories_resources = Path(directory + "\\src\\main\\resources\\")

        if os.path.isdir(Path(directory + "\\src\\main\\java")):
            complete_path_challenge_directories_java = Path(directory + "\\src\\main\\java\\core_files")

            text = generate_prompt_code(complete_path_challenge_directories_java)

        elif os.path.isdir(Path(directory + "\\src\\main\\js")):
            complete_path_challenge_directories_js = Path(directory + "\\src\\main\\js")

            text = generate_prompt_code(complete_path_challenge_directories_js)

        elif os.path.isdir(Path(directory + "\\src\\main\\python")):
            complete_path_challenge_directories_python = Path(directory + "\\src\\main\\python")

            text = generate_prompt_code(complete_path_challenge_directories_python)

        result = parse_code(text)
        parser_ai(call_ai(result), complete_path_challenge_directories_resources)


        try:
            check_compile(directory)
        except Exception:
            print(directory + " failed compiling")


if __name__ == "__main__":
    main()
