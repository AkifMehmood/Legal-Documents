def read_file(file):
    file_type = file.type

    if file_type == "text/plain":
        return file.read().decode("utf-8")
    else:
        return "Unsupported file type. Please upload a .txt file."
