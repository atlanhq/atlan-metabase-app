"""Unit tests for app.asset_mapper.

Guards the contract every published Metabase entity carries:

- name + qualifiedName + sourceURL set from the typed record
- Collection / Dashboard / Question relationship refs use the canonical
  ``connection_qualified_name/<segment>/<id>`` qualifiedName format
- BIProcess inputs/outputs surface as Atlas refs on ``attributes`` so the
  publish-app's ARS resolver finds the lineage (this was the v2-era
  failure mode that motivated the migration)
- QueryIntelligence-only attributes (``metabaseSourceDatabaseName``,
  ``metabaseSourceSchemaName``) ride along on MetabaseQuestion outputs so
  the downstream QI node can JSONPath-read them
- Sync stamp metadata is uniform across mappers
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api_types import (
    BIProcessLineageRecord,
    CollectionRecord,
    DashboardRecord,
    QuestionRecord,
)
from app.asset_mapper import (
    map_bi_process,
    map_collection,
    map_dashboard,
    map_question,
    serialize_entity,
)

CONN_QN = "default/metabase/123"

CTX: dict[str, Any] = dict(
    connection_qualified_name=CONN_QN,
    connection_name="local-test",
    connector_name="metabase",
    workflow_id="wf-1",
    workflow_run_id="run-1",
    last_sync_run_at_ms=1700000000000,
    tenant_id="default",
)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


class TestMapCollection:
    def test_name_and_qualified_name(self):
        rec = CollectionRecord.from_dict({"id": 7, "name": "Marketing"})
        out = serialize_entity(map_collection(rec, **CTX))
        attrs = out["attributes"]
        assert out["typeName"] == "MetabaseCollection"
        assert attrs["name"] == "Marketing"
        assert attrs["qualifiedName"] == f"{CONN_QN}/collections/7"

    def test_source_url_passthrough(self):
        rec = CollectionRecord.from_dict(
            {"id": 7, "name": "Marketing", "sourceURL": "http://m/collection/7"}
        )
        out = serialize_entity(map_collection(rec, **CTX))
        assert out["attributes"]["sourceURL"] == "http://m/collection/7"

    def test_is_personal_from_owner_id(self):
        rec_personal = CollectionRecord.from_dict(
            {"id": 7, "name": "Mine", "personal_owner_id": 42}
        )
        rec_shared = CollectionRecord.from_dict({"id": 8, "name": "Shared"})
        out_personal = serialize_entity(map_collection(rec_personal, **CTX))
        out_shared = serialize_entity(map_collection(rec_shared, **CTX))
        assert out_personal["attributes"]["metabaseIsPersonalCollection"] is True
        assert out_shared["attributes"]["metabaseIsPersonalCollection"] is False

    def test_sync_metadata_stamped(self):
        rec = CollectionRecord.from_dict({"id": 7, "name": "Marketing"})
        out = serialize_entity(map_collection(rec, **CTX))
        attrs = out["attributes"]
        assert attrs["connectorName"] == "metabase"
        assert attrs["connectionName"] == "local-test"
        assert attrs["connectionQualifiedName"] == CONN_QN
        assert attrs["tenantId"] == "default"
        assert attrs["lastSyncWorkflowName"] == "wf-1"
        assert attrs["lastSyncRun"] == "run-1"
        assert attrs["lastSyncRunAt"] == 1700000000000


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class TestMapDashboard:
    def test_name_and_qualified_name(self):
        rec = DashboardRecord.from_dict({"id": 100, "name": "Sales"})
        out = serialize_entity(map_dashboard(rec, **CTX))
        attrs = out["attributes"]
        assert out["typeName"] == "MetabaseDashboard"
        assert attrs["name"] == "Sales"
        assert attrs["qualifiedName"] == f"{CONN_QN}/dashboards/100"

    def test_question_count_from_cards(self):
        rec = DashboardRecord.from_dict({"id": 100, "name": "Sales", "cards_count": 5})
        out = serialize_entity(map_dashboard(rec, **CTX))
        assert out["attributes"]["metabaseQuestionCount"] == 5

    def test_collection_relationship_ref(self):
        rec = DashboardRecord.from_dict(
            {
                "id": 100,
                "name": "Sales",
                "collection": {"id": 7, "name": "Marketing"},
            }
        )
        out = serialize_entity(map_dashboard(rec, **CTX))
        attrs = out["attributes"]
        assert attrs["metabaseCollectionName"] == "Marketing"
        assert attrs["metabaseCollectionQualifiedName"] == f"{CONN_QN}/collections/7"
        # Relation ref carries typeName + uniqueAttributes for Atlas resolution.
        rel = out["relationshipAttributes"]["metabaseCollection"]
        assert rel["typeName"] == "MetabaseCollection"
        assert rel["uniqueAttributes"]["qualifiedName"] == f"{CONN_QN}/collections/7"

    def test_no_collection_means_no_collection_ref(self):
        rec = DashboardRecord.from_dict({"id": 100, "name": "Sales"})
        out = serialize_entity(map_dashboard(rec, **CTX))
        # No collection_id → no relationship ref emitted.
        assert "metabaseCollection" not in out.get("relationshipAttributes", {})


# ---------------------------------------------------------------------------
# Question
# ---------------------------------------------------------------------------


class TestMapQuestion:
    def test_name_and_qualified_name(self):
        rec = QuestionRecord.from_dict({"id": 200, "name": "Top Customers"})
        asset, extras = map_question(rec, **CTX)
        out = serialize_entity(asset, extras)
        attrs = out["attributes"]
        assert out["typeName"] == "MetabaseQuestion"
        assert attrs["name"] == "Top Customers"
        assert attrs["qualifiedName"] == f"{CONN_QN}/questions/200"

    def test_query_metadata_passthrough(self):
        rec = QuestionRecord.from_dict(
            {
                "id": 200,
                "name": "Top Customers",
                "metabase_query": "SELECT * FROM customers",
                "query_type": "native",
            }
        )
        asset, extras = map_question(rec, **CTX)
        out = serialize_entity(asset, extras)
        attrs = out["attributes"]
        assert attrs["metabaseQuery"] == "SELECT * FROM customers"
        assert attrs["metabaseQueryType"] == "native"

    def test_qi_extras_for_lineage(self):
        """QueryIntelligenceNode reads metabaseSourceDatabaseName /
        metabaseSourceSchemaName / metabaseSourceEngine via JSONPath — they
        must land in attributes. The pyatlan_v9 model does not have these
        fields, so the mapper returns them as extras and the serializer
        merges them."""
        rec = QuestionRecord.from_dict(
            {
                "id": 200,
                "name": "Top Customers",
                "metabase_database_name": "analytics_db",
                "metabase_schema_name": "public",
                "metabase_source_engine": "snowflake",
            }
        )
        asset, extras = map_question(rec, **CTX)
        assert extras == {
            "metabaseSourceDatabaseName": "analytics_db",
            "metabaseSourceSchemaName": "public",
            "metabaseSourceEngine": "snowflake",
        }
        out = serialize_entity(asset, extras)
        assert out["attributes"]["metabaseSourceDatabaseName"] == "analytics_db"
        assert out["attributes"]["metabaseSourceSchemaName"] == "public"
        assert out["attributes"]["metabaseSourceEngine"] == "snowflake"

    def test_engine_extra_omitted_when_empty(self):
        """An empty engine must NOT land in attributes — the QI node treats
        a missing ``vendorKey`` lookup as 'fall through to default', which
        is what we want for records the connector couldn't resolve. An
        empty string would route every such query through the empty-string
        parser branch (effectively the same Oracle fallback we're fixing)."""
        rec = QuestionRecord.from_dict(
            {
                "id": 200,
                "name": "Top Customers",
                "metabase_source_engine": "",
            }
        )
        _, extras = map_question(rec, **CTX)
        assert "metabaseSourceEngine" not in extras

    def test_dashboard_relationship_refs(self):
        rec = QuestionRecord.from_dict(
            {
                "id": 200,
                "name": "Top Customers",
                "dashboards": [
                    {"id": 100, "name": "Sales"},
                    {"id": 101, "name": "Ops"},
                ],
            }
        )
        asset, extras = map_question(rec, **CTX)
        out = serialize_entity(asset, extras)
        assert out["attributes"]["metabaseDashboardCount"] == 2
        dashboard_refs = out["relationshipAttributes"]["metabaseDashboards"]
        qns = {r["uniqueAttributes"]["qualifiedName"] for r in dashboard_refs}
        assert qns == {f"{CONN_QN}/dashboards/100", f"{CONN_QN}/dashboards/101"}
        assert all(r["typeName"] == "MetabaseDashboard" for r in dashboard_refs)


# ---------------------------------------------------------------------------
# BIProcess
# ---------------------------------------------------------------------------


class TestMapBIProcess:
    def test_name_and_qualified_name(self):
        rec = BIProcessLineageRecord(
            name="Top Customers", question_id=200, dashboard_ids=[100]
        )
        out = serialize_entity(map_bi_process(rec, **CTX))
        attrs = out["attributes"]
        assert out["typeName"] == "BIProcess"
        assert attrs["name"] == "Top Customers"
        assert attrs["qualifiedName"] == f"{CONN_QN}/questions_dashboards/200"

    def test_inputs_carry_metabase_question_ref(self):
        rec = BIProcessLineageRecord(name="Q", question_id=200, dashboard_ids=[100])
        out = serialize_entity(map_bi_process(rec, **CTX))
        inputs = out["attributes"]["inputs"]
        assert len(inputs) == 1
        assert inputs[0]["typeName"] == "MetabaseQuestion"
        assert (
            inputs[0]["uniqueAttributes"]["qualifiedName"] == f"{CONN_QN}/questions/200"
        )

    def test_outputs_list_metabase_dashboard_refs(self):
        rec = BIProcessLineageRecord(
            name="Q", question_id=200, dashboard_ids=[100, 101, 102]
        )
        out = serialize_entity(map_bi_process(rec, **CTX))
        outputs = out["attributes"]["outputs"]
        assert len(outputs) == 3
        qns = {o["uniqueAttributes"]["qualifiedName"] for o in outputs}
        assert qns == {
            f"{CONN_QN}/dashboards/100",
            f"{CONN_QN}/dashboards/101",
            f"{CONN_QN}/dashboards/102",
        }
        assert all(o["typeName"] == "MetabaseDashboard" for o in outputs)

    def test_from_dict_recovers_dashboard_ids_from_atlas_refs(self):
        """process_assets emits Atlas-shaped refs; the record factory must
        reconstruct typed dashboard_ids so the mapper can rebuild them."""
        raw = {
            "name": "Top Customers",
            "question_id": 200,
            "inputs": [],
            "outputs": [
                {
                    "typeName": "MetabaseDashboard",
                    "uniqueAttributes": {"qualifiedName": f"{CONN_QN}/dashboards/100"},
                },
                {
                    "typeName": "MetabaseDashboard",
                    "uniqueAttributes": {"qualifiedName": f"{CONN_QN}/dashboards/101"},
                },
            ],
        }
        rec = BIProcessLineageRecord.from_dict(raw)
        assert rec.dashboard_ids == ["100", "101"]


# ---------------------------------------------------------------------------
# serialize_entity contract
# ---------------------------------------------------------------------------


class TestSerializeEntity:
    @pytest.mark.parametrize(
        "factory,record",
        [
            (
                map_collection,
                CollectionRecord.from_dict({"id": 7, "name": "Marketing"}),
            ),
            (
                map_dashboard,
                DashboardRecord.from_dict({"id": 100, "name": "Sales"}),
            ),
            (
                map_bi_process,
                BIProcessLineageRecord(name="Q", question_id=200, dashboard_ids=[100]),
            ),
        ],
    )
    def test_emits_typename_status_attributes(self, factory, record):
        out = serialize_entity(factory(record, **CTX))
        assert set(out.keys()) >= {"typeName", "status", "attributes"}
        assert out["status"] == "ACTIVE"
        assert isinstance(out["attributes"], dict)
        assert "name" in out["attributes"]
        assert "qualifiedName" in out["attributes"]
