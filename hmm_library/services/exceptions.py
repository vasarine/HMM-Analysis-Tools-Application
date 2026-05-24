class HMMLibraryError(Exception):
    pass


class RemoteUnavailable(HMMLibraryError):
    pass


class NoPfamModel(HMMLibraryError):
    pass
