import unittest

from studio_api.features.environments.router import router


class EnvironmentsContractTest(unittest.TestCase):
    def test_environment_routes_are_registered_as_one_shared_feature(self):
        paths = {route.path for route in router.routes}
        self.assertEqual(
            paths,
            {
                "/api/v1/environments",
                "/api/v1/environments/requirements",
                "/api/v1/environments/{environment_id}/select",
                "/api/v1/environments/{environment_id}/sync",
                "/api/v1/environments/{environment_id}",
            },
        )


if __name__ == "__main__":
    unittest.main()
