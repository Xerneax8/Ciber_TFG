import random
import subprocess
import sys
import time
import requests
import config
from google import genai
import os
import re
from pathlib import Path
import shutil
import argparse
import yaml


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
                "text": f"Create a {take_style()} frontend for this backend with theme {take_theme()} (invent fake data if necessary), retrieve all the necessary html, js and css"
                        f" (only use percentages here) retrieve only the code and a line above it indicating static/name"
                        f" or template/name, this last one is important, ignore the healthcheck and if a file misses. {text}"
            }]
        }]
    ).text

def check_ai(llm_text, error):
    # Configuring OS to get the API Key
    os.environ['GEMINI_API_KEY'] = config.GEMINI_API_KEY

    client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))


    # Prompt to retrieve the frontend with all the parts
    return client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[{
            "role": "user",
            "parts": [{
                "text": f"You give me this code and it is throwing that error, retrieve the correct code with the same"
                        f"format, this last part is really important.\n{llm_text}\n{error}"
            }]
        }]
    ).text

def check_deployment_and_health(directory, timeout=60):
    """
    Deploys a Docker container using a shell script and checks its health endpoint.
    Automatically reads the healthcheck URL and correct port (host-side) from docker-compose.yml.
    """
    parent_directory = os.getcwd()
    if not parent_directory:
        print("Error: PWD environment variable not set.")
        return False

    compose_path = os.path.join(Path(parent_directory) / Path(directory), "docker-compose.yml")
    if not os.path.exists(compose_path):
        print(f"Error: docker-compose.yml not found in {directory}")
        return False

    try:
        # --- Parse docker-compose.yml ---
        with open(compose_path, "r") as f:
            compose_data = yaml.safe_load(f)

        services = compose_data.get("services", {})
        if not services:
            print("Error: No services defined in docker-compose.yml.")
            return False

        # Get the first service
        service_name, service_data = next(iter(services.items()))
        ports = service_data.get("ports", [])
        healthcheck = service_data.get("healthcheck", {}).get("test", [])

        if not ports:
            print("Error: No ports defined in service.")
            return False

        # Extract host port (the part AFTER ":")
        port_mapping = ports[0]
        parts = port_mapping.split(":")
        if len(parts) != 2:
            print(f"Invalid port mapping format: {port_mapping}")
            return False

        host_port, container_port = parts  # "12104:8080"
        host_port = host_port.strip().strip('"').strip("'")

        # Extract healthcheck URL if defined, else default
        health_url = None
        for part in healthcheck:
            if part.startswith("http"):
                # Replace container port (e.g., 8080) with host port
                health_url = part.replace(container_port, host_port)
                break

        if not health_url:
            # Fallback default
            health_url = f"http://localhost:{host_port}/health"

        print(f"Using healthcheck URL: {health_url}")

        os.chdir(Path(directory))
        print(f"Changed to directory: {os.getcwd()}")

        # --- Run deployment script ---
        print("Running deploy-challenge.sh...")
        subprocess.run(["sh", "deploy-challenge.sh"], check=True, capture_output=True, text=True)
        print("Deployment script executed successfully.")

        # --- Poll health endpoint ---
        print(f"Checking health at {health_url} (timeout: {timeout}s)")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                resp = requests.get(health_url, timeout=5)
                if resp.ok:
                    print(f"Health check PASSED with status {resp.status_code}")
                    return "OK"
                print(f"Health check returned status {resp.status_code}, retrying...")
            except requests.RequestException:
                print("Container not ready yet, retrying...")
            time.sleep(3)

        print(f"Health check FAILED after {timeout} seconds.")
        return "Health check failed"

    except subprocess.CalledProcessError as e:
        error_msg = f"Deployment failed (exit code {e.returncode}):\n{e.stderr}"
        return error_msg

    except Exception as e:
        error_msg = f"Unexpected error during deployment: {str(e)}"
        return error_msg
    finally:
        print("Cleaning up Docker containers (docker compose down)...")
        subprocess.run(["docker", "compose", "down"], capture_output=True)
        os.chdir(parent_directory)
        print(f"Returned to directory: {parent_directory}\n")




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
    # Get all arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("-d","--directory")
    parser.add_argument("-n", "--number")
    parser.add_argument("-r", "--retries")
    args = parser.parse_args()
    num_versions = int(args.number)
    max_retries = int(args.retries)
    directory = args.directory

    if num_versions <= 0:
        print("Number of versions should be greater than zero...")
        exit()

    # Getting all the challenge directories of one folder
    files = os.listdir(directory)
    list_challenge_directories = sorted([file for file in files if
                                         "web" in file and "versions" not in file])  # All the directories with "web" in their name

    text = ""

    # Pipeline to get the backend file, give it to Gemini and write back the answer
    for directory in list_challenge_directories:
        ret_str = check_deployment_and_health(directory)
        if ret_str == "OK":
            dir_versions_name = directory + "-versions"
            dir_versions_complete_path = Path(os.path.dirname(os.path.abspath(sys.argv[0]))) / dir_versions_name

            try:
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
                    llm_text = call_ai(result)
                    parser_ai(llm_text,
                              dir_versions_complete_path / (str(directory) + f"-{num + 1}") / "src" / "main" / "resources")

                    ret_str = check_deployment_and_health(Path(dir_versions_name) / Path(str(directory)+ f"-{num + 1}"))
                    num_retries = 0

                    # Check for errors na create new code if necessary
                    while ret_str != "OK" and num_retries < max_retries:
                        llm_checked_text = check_ai(llm_text, ret_str)
                        parser_ai(llm_checked_text,
                                  dir_versions_complete_path / (
                                              str(directory) + f"-{num + 1}") / "src" / "main" / "resources")

                        ret_str = check_deployment_and_health(Path(dir_versions_name) / Path(str(directory)+ f"-{num + 1}"))
                        num_retries += 1

                    if ret_str != "OK":
                        print(f"Max number of retries reached, problem not solved: {ret_str}")
                    else:
                        print("Exercise " + str(directory) + " crafted")
            except BaseException:
                print(str(directory) + " DONE")
        else:
            print("Exercise " + directory + " can't be deployed for checking, check the code...")



if __name__ == "__main__":
    main()
