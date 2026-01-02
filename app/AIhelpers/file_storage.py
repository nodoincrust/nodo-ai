import os

BASE_STORAGE_PATH = "storage"


def save_document_file(
    company_id: int,
    document_id: int,
    version: int,
    upload_file,
) -> str:
 

    company_dir = os.path.join(
        BASE_STORAGE_PATH,
        "companies",
        str(company_id),
        "documents",
        str(document_id),
    )

    os.makedirs(company_dir, exist_ok=True)

    filename = f"v{version}_{upload_file.filename}"
    file_path = os.path.join(company_dir, filename)

    with open(file_path, "wb") as f:
        f.write(upload_file.file.read())

    return file_path
