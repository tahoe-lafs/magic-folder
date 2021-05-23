

from allmydata.uri import (
    from_string as tahoe_uri_from_string,
    IDirectoryURI,
)


def is_directory_cap(capability):
    """
    :returns: True if `capability` is a directory-cap of any sort
    """
    uri = tahoe_uri_from_string(capability)
    return IDirectoryURI.providedBy(uri)
