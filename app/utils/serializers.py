def to_dict(obj, fields):
    return {field: getattr(obj, field) for field in fields}
