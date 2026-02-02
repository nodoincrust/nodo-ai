import os

BASE_STORAGE_PATH = "storage"                                      # Root directory for all stored files


def ensureDirectory(path: str) -> None:
    os.makedirs(path, exist_ok=True)                               # Ensures directory exists safely


def getCompanyDocumentDir(companyId: int, documentId: int) -> str:
    return os.path.join(
        BASE_STORAGE_PATH,
        "companies",
        str(companyId),
        "documents",
        str(documentId),
    )                                                              # Builds document storage path


def saveDocumentFile(
    *,
    companyId: int,
    documentId: int,
    version: int,
    uploadFile,
) -> str:

    documentDir = getCompanyDocumentDir(companyId, documentId)
    ensureDirectory(documentDir)                                   # Creates document directory if missing

    filename = f"v{version}_{uploadFile.filename}"
    filePath = os.path.join(documentDir, filename)

    with open(filePath, "wb") as file:
        file.write(uploadFile.file.read())                         # Writes uploaded file to disk

    return filePath
