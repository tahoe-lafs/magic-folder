import unicodedata

def normalize(text):
    return unicodedata.normalize("NFC", text)
