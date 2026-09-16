import unittest

from studio_api.features.registry.task_mapper import (
    normalize_account_tasks,
    normalize_participation,
    normalize_registry_task,
)


class RegistryTaskMapperTest(unittest.TestCase):
    def test_separates_database_and_runtime_identifiers(self):
        task = normalize_account_tasks(
            [
                {
                    "_id": "507f1f77bcf86cd799439011",
                    "title": "MNIST",
                    "runtimeKey": "mnist-v2",
                    "ownerHandle": "ccl",
                    "slug": "mnist-digits",
                    "registryStatus": "draft",
                    "runtimeContract": {
                        "name": "federated-task-v3",
                        "schemaVersion": 3,
                    },
                    "yamlConfig": "rounds: 3",
                }
            ]
        )[0]
        self.assertEqual(task["taskId"], "507f1f77bcf86cd799439011")
        self.assertEqual(task["runtimeKey"], "mnist-v2")
        self.assertEqual(task["displayName"], "MNIST")
        self.assertEqual(task["ownerHandle"], "ccl")
        self.assertEqual(task["slug"], "mnist-digits")
        self.assertEqual(task["registryStatus"], "draft")
        self.assertEqual(task["runtimeContract"]["name"], "federated-task-v3")

    def test_drops_items_without_a_stable_id(self):
        self.assertEqual(normalize_account_tasks([{"title": "No ID"}]), [])

    def test_maps_public_registry_task_without_inventing_database_id(self):
        task = normalize_registry_task(
            {
                "id": "registry-owner/mnist",
                "title": "mnist",
                "slug": "mnist",
                "owner": {"handle": "registry-owner"},
                "visibility": "public",
                "tags": "vision, mnist",
                "permissions": {
                    "canView": True,
                    "canRequestParticipation": True,
                },
            }
        )
        self.assertEqual(task["registryId"], "registry-owner/mnist")
        self.assertIsNone(task["taskId"])
        self.assertEqual(task["ownerHandle"], "registry-owner")
        self.assertEqual(task["tags"], ["vision", "mnist"])
        self.assertTrue(task["permissions"]["canRequestParticipation"])
        self.assertFalse(task["permissions"]["canOpenWorkspace"])
        self.assertEqual(task["runtimeContract"]["name"], "legacy-v1")

    def test_uses_primary_model_as_registry_title_without_losing_task_name(self):
        task = normalize_registry_task(
            {
                "id": "ccl/ecg-task",
                "title": "ECG Federated Study",
                "displayName": "ECG Federated Study",
                "ownerHandle": "ccl",
                "slug": "ecg-task",
                "primaryModel": {
                    "displayName": "ECG Rhythm Classifier",
                    "framework": "pytorch",
                },
                "taskCategory": "classification",
                "dataModality": "timeseries",
            }
        )

        self.assertEqual(task["title"], "ECG Rhythm Classifier")
        self.assertEqual(task["displayName"], "ECG Federated Study")
        self.assertEqual(task["primaryModel"]["framework"], "pytorch")

    def test_maps_authorized_task_identity_and_membership(self):
        task = normalize_registry_task(
            {
                "_id": "507f1f77bcf86cd799439011",
                "taskId": "507f1f77bcf86cd799439011",
                "title": "ownedtask",
                "runtimeKey": "ownedtask",
                "ownerHandle": "ccl",
                "slug": "ownedtask",
                "membership": {"role": "owner", "status": "owner"},
                "permissions": {"isOwner": True, "canManage": True},
                "runtimeContract": {
                    "name": "federated-task-v2",
                    "schemaVersion": 2,
                    "fedopsVersion": "1.1.30.14",
                },
            }
        )
        self.assertEqual(task["taskId"], "507f1f77bcf86cd799439011")
        self.assertEqual(task["runtimeKey"], "ownedtask")
        self.assertEqual(task["membership"]["role"], "owner")
        self.assertTrue(task["permissions"]["canOpenWorkspace"])
        self.assertEqual(task["runtimeContract"]["name"], "federated-task-v2")

    def test_maps_admin_management_without_inventing_ownership(self):
        task = normalize_registry_task(
            {
                "taskId": "507f1f77bcf86cd799439011",
                "title": "another-users-task",
                "ownerHandle": "actual-owner",
                "slug": "another-users-task",
                "permissions": {
                    "isOwner": False,
                    "isAdmin": True,
                    "canManage": True,
                    "canOpenWorkspace": True,
                },
            }
        )

        self.assertEqual(task["membership"], {"role": "admin", "status": "admin"})
        self.assertFalse(task["permissions"]["isOwner"])
        self.assertTrue(task["permissions"]["isAdmin"])
        self.assertTrue(task["permissions"]["canManage"])

    def test_maps_admin_participation_without_losing_admin_permission(self):
        task = normalize_registry_task(
            {
                "taskId": "507f1f77bcf86cd799439011",
                "title": "another-users-task",
                "ownerHandle": "actual-owner",
                "slug": "another-users-task",
                "permissions": {
                    "isOwner": False,
                    "isAdmin": True,
                    "canManage": True,
                    "canRequestParticipation": False,
                    "participationStatus": "requested",
                },
            }
        )

        self.assertEqual(
            task["membership"],
            {"role": "participant", "status": "requested"},
        )
        self.assertTrue(task["permissions"]["isAdmin"])
        self.assertFalse(task["permissions"]["isOwner"])

    def test_classifies_llm_tasks_for_agent_base_model_selection(self):
        llm = normalize_registry_task({
            "id": "ccl/federated-llm",
            "title": "Federated LLM",
            "dataType": "LLM",
        })
        classifier = normalize_registry_task({
            "id": "ccl/mnist",
            "title": "MNIST",
            "dataType": "Image",
            "primaryModel": {"task": "classification"},
        })

        self.assertEqual(llm["modelCapability"], "base-llm")
        self.assertEqual(classifier["modelCapability"], "tool-ai")

    def test_explicit_model_type_wins_over_legacy_data_inference(self):
        classifier = normalize_registry_task({
            "id": "ccl/text-classifier",
            "title": "Text Classifier",
            "modelType": "AI",
            "dataType": "LLM",
        })
        llm = normalize_registry_task({
            "id": "ccl/vision-llm",
            "title": "Vision LLM",
            "modelType": "LLM",
            "dataModality": "image",
        })

        self.assertEqual(classifier["modelCapability"], "tool-ai")
        self.assertEqual(llm["modelCapability"], "base-llm")

    def test_maps_requested_participation_to_studio_contract(self):
        participation = normalize_participation(
            {"_id": "participation-1", "taskId": "mongo-task-1", "status": "requested"},
            {"taskId": None, "runtimeKey": None},
        )

        self.assertEqual(
            participation,
            {
                "participationId": "participation-1",
                "taskId": "mongo-task-1",
                "status": "pending-approval",
                "runtimeKey": None,
                "localProjectId": None,
            },
        )

    def test_maps_participant_leave_policy_from_fedops_web(self):
        task = normalize_registry_task({
            "id": "owner/mnist",
            "taskId": "task-1",
            "title": "MNIST",
            "permissions": {
                "participationStatus": "approved",
                "isParticipant": True,
                "canLeaveParticipation": False,
                "leaveRequiresCompletedRun": True,
                "completedParticipationCount": 0,
            },
        })

        self.assertFalse(task["permissions"]["canLeaveParticipation"])
        self.assertTrue(task["permissions"]["leaveRequiresCompletedRun"])
        self.assertEqual(task["permissions"]["completedParticipationCount"], 0)


if __name__ == "__main__":
    unittest.main()
