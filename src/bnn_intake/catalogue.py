"""Addressed catalogue access. The catalogue is explicitly open, not a capability gate."""
import json
from importlib.resources import files
from .contracts import METHOD_ACTIONS, validate


class Catalogue:
    def __init__(self):
        data = json.loads(files("bnn_intake").joinpath("data/framework-catalogue.json").read_text())
        self.entries = {e["id"]: e for e in data["entries"]}
        self.version = data["version"]
        self.classification = data["classification"]

    def call(self, args):
        validate(args, METHOD_ACTIONS)
        if args["action"] == "classification":
            values = self.classification[args["dimension"]]
            if isinstance(values, dict):
                values = [{"id": key, "meaning": value} for key, value in values.items()]
            if args["ids"]:
                values = [v for v in values if v["id"] in args["ids"]]
            start, limit = args["offset"], args["limit"]
            return {"catalogue_version": self.version, "dimension": args["dimension"],
                    "items": values[start:start + limit], "total": len(values),
                    "next_offset": start + limit if start + limit < len(values) else None,
                    "local_extensions_allowed": True, "classification_is_not_a_capability_gate": True}
        if args["action"] == "read":
            if len(args["ids"]) > 12:
                raise ValueError("intake_addressed_batch_required")
            return {"catalogue_version": self.version,
                    "cards": [self.entries[x] for x in args["ids"] if x in self.entries],
                    "missing": [x for x in args["ids"] if x not in self.entries],
                    "unlisted_analytical_approaches_allowed": True,
                    "missing_id_is_not_a_method_prohibition": True}
        terms = args["query"].casefold().split()
        candidates = []
        for e in self.entries.values():
            if args.get("family") and args["family"] not in [e["primary_family"], *e["secondary_families"]]:
                continue
            text = " ".join([e["name"], e["specific_decomposition_operation"], e["typed_output"]["description"]]).casefold()
            if all(term in text for term in terms):
                candidates.append({"id": e["id"], "name": e["name"], "family": e["primary_family"],
                                   "operation": e["specific_decomposition_operation"]})
        start, limit = args.get("offset", 0), args.get("limit", 5)
        page = candidates[start:start + limit]
        return {"matches": page, "matched_total": len(candidates), "offset": start,
                "truncated": start + len(page) < len(candidates),
                "next_offset": start + len(page) if start + len(page) < len(candidates) else None,
                "search_type": "addressed_lexical_filter_not_semantic_ranking",
                "unlisted_analytical_approaches_allowed": True}
