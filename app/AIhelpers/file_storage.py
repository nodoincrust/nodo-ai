import os

BASE_STORAGE_PATH = "storage"


def saveDocumentFile(
    companyId: int,
    document_id: int,
    version: int,
    uploadFile,
) -> str:
    companyDirectory = os.path.join(
        BASE_STORAGE_PATH,
        "companies",
        str(companyId),
        "documents",
        str(document_id),
    )

    os.makedirs(companyDirectory, exist_ok=True)

    filename = f"v{version}_{uploadFile.filename}"
    filePath = os.path.join(companyDirectory, filename)

    with open(filePath, "wb") as file:
        file.write(uploadFile.file.read())

    return filePath