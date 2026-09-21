from django.test import SimpleTestCase


class OpenAPITests(SimpleTestCase):
    def test_docs_and_schema_include_current_endpoints_without_refresh_token_bodies(self):
        docs = self.client.get("/api/docs/")
        self.assertEqual(docs.status_code, 200)

        response = self.client.get("/api/schema/?format=json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertEqual(set(schema["paths"]), {
            "/api/auth/login/", "/api/auth/logout/", "/api/auth/refresh/",
            "/api/auth/register/", "/api/auth/send-otp/",
            "/api/auth/verify-otp/", "/api/auth/verify/",
            "/api/shop/products/", "/api/shop/test/",
        })
        for component in ("LoginResponse", "RefreshResponse"):
            self.assertEqual(
                set(schema["components"]["schemas"][component]["properties"]),
                {"access", "user"},
            )
        self.assertNotIn(
            "requestBody", schema["paths"]["/api/auth/refresh/"]["post"]
        )
        for route, cookie in (
            ("/api/auth/refresh/", "refresh_token"),
            ("/api/auth/register/", "registration_token"),
        ):
            parameters = schema["paths"][route]["post"]["parameters"]
            self.assertIn(
                {"name": cookie, "in": "cookie"},
                [{"name": item["name"], "in": item["in"]} for item in parameters],
            )
        catalog = schema["paths"]["/api/shop/products/"]["get"]
        self.assertEqual(
            {parameter["name"] for parameter in catalog["parameters"]},
            {"page", "q"},
        )
        self.assertEqual(
            catalog["responses"]["200"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/PaginatedProducts"},
        )
