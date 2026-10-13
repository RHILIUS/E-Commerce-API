from django.test import SimpleTestCase


class OpenAPITests(SimpleTestCase):
    def test_docs_and_schema_include_current_endpoints_without_refresh_token_bodies(self):
        docs = self.client.get("/api/docs/")
        self.assertEqual(docs.status_code, 200)

        response = self.client.get("/api/schema/?format=json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertEqual(set(schema["paths"]), {
            "/api/account/change-password/request/", "/api/account/change-password/resend/", "/api/account/change-password/confirm/",
            "/api/account/profile/photo/", "/api/account/profile/", "/api/account/recipients/", "/api/account/recipients/{id}/",
            "/api/shop/checkout/preview/",
            "/api/shop/orders/", "/api/shop/orders/{id}/cancel/", "/api/shop/orders/{id}/history/",
            "/api/shop/checkouts/", "/api/shop/checkouts/{reference}/",
            "/api/shop/checkouts/stripe/", "/api/shop/checkouts/{reference}/payment/",
            "/api/shop/checkouts/{reference}/payment/cancel/", "/api/shop/stripe/webhook/",
            "/api/auth/login/", "/api/auth/logout/", "/api/auth/refresh/",
            "/api/auth/register/", "/api/auth/send-otp/",
            "/api/auth/verify-otp/", "/api/auth/verify/",
            "/api/shop/products/", "/api/shop/products/{id}/", "/api/shop/test/",
            "/api/shop/stores/{id}/", "/api/shop/stores/{id}/home/", "/api/shop/stores/{id}/products/",
            "/api/shop/carts/", "/api/shop/carts/items/", "/api/shop/carts/items/{id}/",
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
                [{"name": item["name"], "in": item["in"]}
                    for item in parameters],
            )
        catalog = schema["paths"]["/api/shop/products/"]["get"]
        self.assertIn(
            "display_price", schema["components"]["schemas"]["ProductCatalog"]["properties"])
        self.assertEqual(set(schema["components"]["schemas"]["DisplayPrice"]["properties"]), {
                         "amount", "currency"})
        self.assertEqual(
            {parameter["name"] for parameter in catalog["parameters"]},
            {"page", "q"},
        )
        self.assertEqual(
            catalog["responses"]["200"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/PaginatedProducts"},
        )
        detail = schema["paths"]["/api/shop/products/{id}/"]["get"]
        self.assertEqual(set(detail["responses"]), {"200", "401", "404"})
        self.assertIn({}, detail["security"])
        self.assertEqual(
            detail["responses"]["200"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/ProductDetail"},
        )
        self.assertEqual(schema["components"]["schemas"]["ProductDetail"]
                         ["properties"]["can_purchase"]["type"], "boolean")

        orders = schema["paths"]["/api/shop/orders/"]["get"]
        self.assertEqual({p["name"] for p in orders["parameters"]}, {"tab", "page"})
        self.assertNotIn({}, orders["security"])

        store_catalog = schema["paths"]["/api/shop/stores/{id}/products/"]["get"]
        self.assertIn({}, store_catalog["security"])
        self.assertEqual({p["name"] for p in store_catalog["parameters"]}, {"id", "page", "q", "sort", "category"})
        self.assertEqual(set(store_catalog["responses"]), {"200", "400", "401", "404"})
        public_store = schema["components"]["schemas"]["PublicStore"]["properties"]
        self.assertTrue({"public_email", "public_phone", "items_sold"} <= set(public_store))
        self.assertNotIn("members", public_store)

        profile = schema["paths"]["/api/account/profile/"]
        self.assertEqual(set(profile), {"get", "put"})
        self.assertNotIn({}, profile["get"]["security"])
        self.assertNotIn({}, profile["put"]["security"])
        self.assertEqual(set(profile["put"]["responses"]), {"200", "400", "401"})
        self.assertEqual(profile["put"]["requestBody"]["content"]["application/json"]["schema"],
                         {"$ref": "#/components/schemas/ProfileUpdate"})
        self.assertEqual(set(schema["components"]["schemas"]["ProfileUpdate"]["required"]),
                         {"full_name", "phone_number", "address"})

        photo = schema["paths"]["/api/account/profile/photo/"]
        self.assertEqual(set(photo), {"get", "put", "delete"})
        for operation in photo.values():
            self.assertNotIn({}, operation["security"])
        upload_schema = schema["components"]["schemas"]["PhotoUpload"]
        self.assertEqual(upload_schema["properties"]["image"]["format"], "binary")
        self.assertEqual(upload_schema["properties"]["crop"]["type"], "string")
