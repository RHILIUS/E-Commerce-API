# Seeding development data

## How to seed

Run these commands from the backend repository root with the project dependencies installed and PostgreSQL running:

```bash
source venv/bin/activate
python manage.py migrate
python manage.py import_taxonomy --directory data/taxonomy/2026-08
python manage.py seed
```

The command uses the database and storage configured in your environment. With `USE_AZURE_STORAGE=False`, images are saved under `media/`. With `USE_AZURE_STORAGE=True`, configure `AZURE_ACCOUNT_NAME`, `AZURE_ACCOUNT_KEY`, and `AZURE_CONTAINER`; the command uploads images to that container.

Keep the source images under `static/seed_images/` available even when rerunning the command: all source assets are validated before seeding begins. Paths in `core/seed_data.py` are relative to the backend root unless explicitly absolute.

## What the command creates

Seed data uses two lists: `CUSTOMERS_DATA` contains users, their
`recipient_infos`, and `orders`; `STORES_DATA` contains each store's
`address`, `members` (role and nested user with recipients), and `products`.
Products are seeded before customer orders. Product names must be unique across
the seed catalogs because order items and image folders identify them by name.

See [checkout fixture behavior](checkout/development.md#seed-purchase-behavior) and
[model structures](checkout/models.md) for checkout, payment and reservation details.

Each sample order has a unique non-empty string `key` for repeatable development seeding.
Order IDs are positive values assigned by the database. The stable key determines
the checkout UUID; reruns find the order by checkout and store. Keep fixture keys
unchanged to avoid creating another order.

Existing negative-ID fixtures are not converted or deleted automatically. For
disposable development databases created by the older seeder, rebuild the database,
apply migrations, import the taxonomy, and seed again.
Keep this key stable across reruns. Each order now gets a stable COD checkout,
explicit unpaid/paid payment status, one store, rate 1 and complete purchase
snapshots in that store's currency. A fixture order cannot span stores. The current
sample explicitly marks payment paid. Orders use their user's default recipient;
item purchase prices come from seeded products. Identical reruns preserve checkout/order/item IDs and timestamps and restore fixture
values. Changing the agreed checkout total/currency requires a new fixture key or
a disposable reset; populated checkout payment terms cannot be rewritten. They do not adjust stock or sold counts to simulate checkout.
Delivered and cancelled orders receive a completion timestamp on first seeding,
which is preserved on reruns. Other statuses have no completion timestamp.
An existing fixture checkout or order belonging to another user causes the transaction to fail.
Additional non-default recipients are matched by their complete address/contact
data; changing that data creates another recipient. Exactly one seeded recipient
per user must be marked default.

The command creates or updates a customer and a store owner, their default delivery recipients, a store, its address and owner membership, products, product specifications, and product images. Seed values come from `core/seed_data.py`; the tables below document those values. Editing this Markdown file alone does not change what gets seeded.

## Product categories and specifications

Import both Shopify 2026-08 JSON files before seeding (see [taxonomy.md](taxonomy.md)).
Each product record in `core/seed_data.py` contains a `category_taxonomy_id` and an
`attribute_values` list. Each choice identifies the attribute by Shopify ID and
handle, and its value by Shopify value ID. Local database primary keys are resolved
at runtime.

| Seed products               | Shopify category     | Category ID suffix |
| --------------------------- | -------------------- | ------------------ |
| Six phones                  | Smart Phones         | `el-4-8-5-2`       |
| Six tablets                 | Tablet Computers     | `el-6-8`           |
| Six laptops                 | Laptops              | `el-6-6`           |
| VisionDesk 24               | All-in-One Computers | `el-6-12`          |
| Other five desktop products | Desktop Computers    | `el-6-3`           |

Specifications use official choices such as OLED/AMOLED/LCD, rugged/foldable phone
form, SSD/NVMe storage, integrated/dedicated graphics, Full HD, DDR5, and computer
form where supported by the descriptions. Tablets receive Battery-powered as their
portable-device power source. RAM capacity and storage capacity remain in the
descriptions; those attributes are not assigned to these categories in this snapshot.

The command resolves active categories, allowed category–attribute links, and
predefined values before creating records or uploading images. Missing taxonomy
causes an actionable error; the seeder does not invent missing definitions.
References are locked for the seed transaction. Re-running updates seeded values
without duplicating specifications and retains additional manually added specs.
If a category change conflicts with existing specifications, validation rejects it
and the database transaction rolls back; review those specifications before retrying.

The store membership grants the seeded store owner the configured role; the customer does not receive a store membership. Neither user receives Django staff or superuser access.

On success, the command prints `Seed complete` with the product, product image, and newly uploaded file counts.

## Running it again

Run `python manage.py seed` again to update matching records. The command matches each user by email, the store by name, products by store and name, and product images by product and order.

- Seeded fields, including product stock, prices, and sold counts, are restored to their seed values. Use this command against your intended development database.
- An existing user's password is preserved.
- Images are uploaded only when their model field is empty; existing image references are not replaced or checked for missing storage files.
- Records removed from the seed data are not automatically deleted. Renaming seed products or the store can create new records.

## Adding or changing seed images

Update `core/seed_data.py` for customer, store-owner, and store image paths. For products, use one folder per product under `static/seed_images/products/image/`, with each folder name exactly matching its product name. Extra or missing product folders cause validation to fail.

Each product folder must contain exactly one image ending in `_default` before its extension. Additional images must end in `_1`, `_2`, and so on, without gaps or duplicate numbers. Supported extensions are `.jpg`, `.jpeg`, `.png`, and `.webp`.

If seeding fails, check the reported source path or naming error, database connectivity, or storage configuration. Database writes run in one transaction; on failure, the command rolls back those writes and attempts to delete newly uploaded files.

---

# Seed values

## Users

> The customer and the owner of the store

| username   | email                  | full_name             | password      | profile_image                                      | profile_image_crop | phone_number  |
| ---------- | ---------------------- | --------------------- | ------------- | -------------------------------------------------- | ------------------ | ------------- |
| rjbaldomar | rjbaldomar23@gmail.com | RJ Baldomar           | rjbaldomar123 | static/seed_images/users/profile/rj_profile.jpg    |                    | +639987654321 |
| rhilius    | rhilius@test.com       | Rhian Jolius Baldomar | rhilius123    | static/seed_images/users/profile/rhian_profile.jpg |                    | +639123456789 |

## RecipientInfo

| name                  | phone_number  | is_default | country_code | address_line1                     | address_line2 | city          | region       | postal_code | user_id                 |
| --------------------- | ------------- | ---------- | ------------ | --------------------------------- | ------------- | ------------- | ------------ | ----------- | ----------------------- |
| RJ Baldomar           | +639987654321 | true       | PH           | 123 Rizal St. Brgy. Mabuhay       |               | Mindanao City | Metro Hirang | 1234        | _references rjbaldomar_ |
| Rhian Jolius Baldomar | +639123456789 | true       | PH           | 200 Magsaysay St. Brgy. Makabayan |               | Luzon City    | Metro Hirang | 1234        | _references rhilius_    |

## Store

Store currency is assigned automatically from its country. The PH seed store uses
PHP, so its product price values are pesos; seeding does not convert these amounts.
The public contact details are sample business details shown on the customer Store Page.

| name               | description                                                                                                           | country_code | profile_image                                                     | profile_image_crop | banner_image                                                    | banner_image_crop | public_email                | public_phone     |
| ------------------ | --------------------------------------------------------------------------------------------------------------------- | ------------ | ----------------------------------------------------------------- | ------------------ | --------------------------------------------------------------- | ----------------- | --------------------------- | ---------------- |
| RhiLius Tech Store | We sell gadgets like tablets, mobile phones, laptops, and computers. Make sure to check us out and have fun shopping! | PH           | /static/seed_images/stores/profile/rhilius_tech_store_profile.jpg | N/A                | /static/seed_images/stores/banner/rhilius_tech_store_banner.jpg | N/A               | contact@rhiliustech.example | +63 917 555 0134 |

## StoreAddress

| country_code | address_line1                     | address_line2 | city       | region       | postal_code | store_id                        |
| ------------ | --------------------------------- | ------------- | ---------- | ------------ | ----------- | ------------------------------- |
| PH           | 200 Magsaysay St. Brgy. Makabayan | N/A           | Luzon City | Metro Hirang | 1234        | _references RhiLius Tech Store_ |

## StoreMember

| role  | store_id                        | user_id              |
| ----- | ------------------------------- | -------------------- |
| admin | _references RhiLius Tech Store_ | _references rhilius_ |

## Product

> All products reference the RhiLius Tech Store defined above and use the `active` status.

| name                       | short_description                             | description                                                                                                                  | stock | price | sold_count | status | store_id                        |
| -------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ----- | ----- | ---------- | ------ | ------------------------------- |
| Rhilius NovaPhone X1       | Flagship phone for photos and performance     | 6.7-inch 120Hz OLED display, OctaCore X1 chip, 12GB RAM, 256GB storage, 50MP triple camera, and 5,000mAh battery.            | 28    | 899   | 96         | active | _references RhiLius Tech Store_ |
| Rhilius PulsePhone 5G      | Fast everyday phone with all-day battery      | 6.5-inch AMOLED display, Pulse P8 processor, 8GB RAM, 128GB storage, 64MP dual camera, 5G, and 5,200mAh battery.             | 42    | 499   | 121        | active | _references RhiLius Tech Store_ |
| Rhilius TitanPhone Pro     | Rugged phone built for demanding work         | 6.6-inch reinforced display, Titan T9 processor, 12GB RAM, 256GB storage, water resistance, and 6,500mAh battery.            | 19    | 749   | 58         | active | _references RhiLius Tech Store_ |
| Rhilius AeroPhone Mini     | Compact phone with flagship features          | 5.9-inch 120Hz OLED display, Aero A7 chip, 8GB RAM, 128GB storage, 48MP dual camera, and wireless charging.                  | 35    | 599   | 83         | active | _references RhiLius Tech Store_ |
| Rhilius VisionPhone Fold   | Foldable phone for work and entertainment     | 7.8-inch foldable OLED display, Vision V2 chip, 12GB RAM, 512GB storage, triple camera, and 4,800mAh battery.                | 14    | 1399  | 37         | active | _references RhiLius Tech Store_ |
| Rhilius LitePhone A5       | Affordable phone for essential daily tasks    | 6.4-inch LCD display, Lite L5 processor, 6GB RAM, 128GB storage, 50MP main camera, and 5,000mAh battery.                     | 50    | 299   | 146        | active | _references RhiLius Tech Store_ |
| Rhilius TechPad 10         | Versatile tablet for study and entertainment  | 10.5-inch 2K display, TechCore T10 chip, 8GB RAM, 128GB storage, quad speakers, Wi-Fi 6, and stylus support.                 | 38    | 399   | 112        | active | _references RhiLius Tech Store_ |
| Rhilius TechPad Pro 13     | Large professional tablet for creative work   | 13-inch 120Hz OLED display, TechCore Pro chip, 12GB RAM, 256GB storage, six speakers, Wi-Fi 7, and Pro Pen support.          | 18    | 849   | 54         | active | _references RhiLius Tech Store_ |
| Rhilius TechPad Mini 8     | Compact tablet for reading and travel         | 8.3-inch 2K display, TechCore M8 chip, 6GB RAM, 128GB storage, stereo speakers, and a 6,000mAh battery.                      | 44    | 329   | 98         | active | _references RhiLius Tech Store_ |
| Rhilius SketchPad 12       | Pen-first tablet for artists and designers    | 12.4-inch color-accurate display, Canvas C4 chip, 12GB RAM, 256GB storage, and a pressure-sensitive Sketch Pen.              | 21    | 699   | 63         | active | _references RhiLius Tech Store_ |
| Rhilius StudyPad 11        | Student tablet with keyboard and stylus       | 11-inch 90Hz display, StudyCore S6 chip, 8GB RAM, 128GB storage, detachable keyboard, stylus, and parental controls.         | 46    | 449   | 130        | active | _references RhiLius Tech Store_ |
| Rhilius GamePad X          | High-refresh tablet made for mobile gaming    | 11.5-inch 144Hz display, GameCore GX chip, 12GB RAM, 256GB storage, vapor cooling, and a 10,000mAh battery.                  | 24    | 649   | 76         | active | _references RhiLius Tech Store_ |
| Rhilius AeroBook 13        | Thin laptop with long battery life            | 13.3-inch 2.5K display, AeroCore A8 processor, 16GB RAM, 512GB SSD, backlit keyboard, and up to 18 hours of battery life.    | 32    | 799   | 88         | active | _references RhiLius Tech Store_ |
| Rhilius WorkBook 15        | Reliable laptop for office productivity       | 15.6-inch Full HD display, WorkCore W7 processor, 16GB RAM, 512GB SSD, Wi-Fi 6E, HDMI, and USB-C connectivity.               | 40    | 699   | 119        | active | _references RhiLius Tech Store_ |
| Rhilius CreatorBook Pro 16 | Powerful laptop for demanding creative work   | 16-inch 3.2K OLED display, CreatorCore C9 processor, Studio G8 graphics, 32GB RAM, 1TB SSD, and dual cooling fans.           | 16    | 1699  | 43         | active | _references RhiLius Tech Store_ |
| Rhilius GameBook RX 15     | Gaming laptop with fast dedicated graphics    | 15.6-inch 165Hz display, GameCore R9 processor, Radeon RX graphics, 16GB RAM, 1TB SSD, and RGB keyboard.                     | 20    | 1399  | 67         | active | _references RhiLius Tech Store_ |
| Rhilius FlexBook 14        | Convertible touchscreen laptop with pen       | 14-inch 2.8K touchscreen, FlexCore F7 processor, 16GB RAM, 512GB SSD, 360-degree hinge, and included digital pen.            | 27    | 999   | 72         | active | _references RhiLius Tech Store_ |
| Rhilius CloudBook Lite     | Lightweight laptop for school and browsing    | 14-inch Full HD display, CloudCore C5 processor, 8GB RAM, 256GB SSD, HD webcam, Wi-Fi 6, and up to 14 hours of battery life. | 48    | 449   | 137        | active | _references RhiLius Tech Store_ |
| Rhilius CoreBox Mini       | Compact desktop for home and office use       | CoreBox C7 processor, integrated graphics, 16GB RAM, 512GB SSD, Wi-Fi 6E, Gigabit Ethernet, HDMI, and USB-C ports.           | 34    | 599   | 91         | active | _references RhiLius Tech Store_ |
| Rhilius VisionDesk 24      | Sleek all-in-one desktop with vivid display   | 24-inch 4K display, VisionCore V7 processor, 16GB RAM, 512GB SSD, 1080p webcam, stereo speakers, and wireless accessories.   | 23    | 999   | 61         | active | _references RhiLius Tech Store_ |
| Rhilius TitanTower X       | Expandable desktop for heavy workloads        | TitanCore T9 processor, Titan GX80 graphics, 32GB DDR5 RAM, 1TB NVMe SSD, liquid cooling, and an upgradeable tower chassis.  | 15    | 1799  | 46         | active | _references RhiLius Tech Store_ |
| Rhilius CreatorStation Pro | Professional workstation for media production | CreatorCore Pro processor, Studio GX90 graphics, 64GB ECC RAM, 2TB NVMe SSD, 10Gb Ethernet, and multi-display support.       | 9     | 2799  | 25         | active | _references RhiLius Tech Store_ |
| Rhilius GameStation R7     | Gaming desktop with high-frame-rate graphics  | GameCore R7 processor, Radeon RX graphics, 32GB DDR5 RAM, 1TB NVMe SSD, Wi-Fi 7, RGB lighting, and liquid cooling.           | 17    | 1599  | 52         | active | _references RhiLius Tech Store_ |
| Rhilius OfficeBox S        | Practical desktop for everyday business tasks | OfficeCore S5 processor, integrated graphics, 16GB RAM, 512GB SSD, Wi-Fi 6, Gigabit Ethernet, and dual-monitor support.      | 41    | 549   | 108        | active | _references RhiLius Tech Store_ |

### ProductImage

| is_default | order | alt_text                                         | product_id                              | crop | image                                                                                             |
| ---------- | ----- | ------------------------------------------------ | --------------------------------------- | ---- | ------------------------------------------------------------------------------------------------- |
| true       | 0     | Rhilius NovaPhone X1 default product image       | _references Rhilius NovaPhone X1_       |      | static/seed_images/products/image/Rhilius NovaPhone X1/rhiliusnovaphonex1_default.jpg             |
| false      | 1     | Rhilius NovaPhone X1 product image 1             | _references Rhilius NovaPhone X1_       |      | static/seed_images/products/image/Rhilius NovaPhone X1/rhiliusnovaphonex1_1.jpg                   |
| false      | 2     | Rhilius NovaPhone X1 product image 2             | _references Rhilius NovaPhone X1_       |      | static/seed_images/products/image/Rhilius NovaPhone X1/rhiliusnovaphonex1_2.jpg                   |
| true       | 0     | Rhilius PulsePhone 5G default product image      | _references Rhilius PulsePhone 5G_      |      | static/seed_images/products/image/Rhilius PulsePhone 5G/rhiliuspulsephone5g_default.jpg           |
| false      | 1     | Rhilius PulsePhone 5G product image 1            | _references Rhilius PulsePhone 5G_      |      | static/seed_images/products/image/Rhilius PulsePhone 5G/rhiliuspulsephone5g_1.jpg                 |
| false      | 2     | Rhilius PulsePhone 5G product image 2            | _references Rhilius PulsePhone 5G_      |      | static/seed_images/products/image/Rhilius PulsePhone 5G/rhiliuspulsephone5g_2.jpg                 |
| true       | 0     | Rhilius TitanPhone Pro default product image     | _references Rhilius TitanPhone Pro_     |      | static/seed_images/products/image/Rhilius TitanPhone Pro/rhiliustitanphonepro_default.jpg         |
| false      | 1     | Rhilius TitanPhone Pro product image 1           | _references Rhilius TitanPhone Pro_     |      | static/seed_images/products/image/Rhilius TitanPhone Pro/rhiliustitanphonepro_1.jpg               |
| false      | 2     | Rhilius TitanPhone Pro product image 2           | _references Rhilius TitanPhone Pro_     |      | static/seed_images/products/image/Rhilius TitanPhone Pro/rhiliustitanphonepro_2.jpg               |
| true       | 0     | Rhilius AeroPhone Mini default product image     | _references Rhilius AeroPhone Mini_     |      | static/seed_images/products/image/Rhilius AeroPhone Mini/rhiliusaerophonemini_default.jpg         |
| false      | 1     | Rhilius AeroPhone Mini product image 1           | _references Rhilius AeroPhone Mini_     |      | static/seed_images/products/image/Rhilius AeroPhone Mini/rhiliusaerophonemini_1.jpg               |
| false      | 2     | Rhilius AeroPhone Mini product image 2           | _references Rhilius AeroPhone Mini_     |      | static/seed_images/products/image/Rhilius AeroPhone Mini/rhiliusaerophonemini_2.jpg               |
| true       | 0     | Rhilius VisionPhone Fold default product image   | _references Rhilius VisionPhone Fold_   |      | static/seed_images/products/image/Rhilius VisionPhone Fold/rhiliusvisionphonefold_default.jpg     |
| false      | 1     | Rhilius VisionPhone Fold product image 1         | _references Rhilius VisionPhone Fold_   |      | static/seed_images/products/image/Rhilius VisionPhone Fold/rhiliusvisionphonefold_1.jpg           |
| false      | 2     | Rhilius VisionPhone Fold product image 2         | _references Rhilius VisionPhone Fold_   |      | static/seed_images/products/image/Rhilius VisionPhone Fold/rhiliusvisionphonefold_2.jpg           |
| true       | 0     | Rhilius LitePhone A5 default product image       | _references Rhilius LitePhone A5_       |      | static/seed_images/products/image/Rhilius LitePhone A5/rhiliuslitephonea5_default.jpg             |
| false      | 1     | Rhilius LitePhone A5 product image 1             | _references Rhilius LitePhone A5_       |      | static/seed_images/products/image/Rhilius LitePhone A5/rhiliuslitephonea5_1.jpg                   |
| false      | 2     | Rhilius LitePhone A5 product image 2             | _references Rhilius LitePhone A5_       |      | static/seed_images/products/image/Rhilius LitePhone A5/rhiliuslitephonea5_2.jpg                   |
| true       | 0     | Rhilius TechPad 10 default product image         | _references Rhilius TechPad 10_         |      | static/seed_images/products/image/Rhilius TechPad 10/rhiliustechpad10_default.jpg                 |
| false      | 1     | Rhilius TechPad 10 product image 1               | _references Rhilius TechPad 10_         |      | static/seed_images/products/image/Rhilius TechPad 10/rhiliustechpad10_1.jpg                       |
| true       | 0     | Rhilius TechPad Pro 13 default product image     | _references Rhilius TechPad Pro 13_     |      | static/seed_images/products/image/Rhilius TechPad Pro 13/rhiliustechpadpro13_default.jpg          |
| false      | 1     | Rhilius TechPad Pro 13 product image 1           | _references Rhilius TechPad Pro 13_     |      | static/seed_images/products/image/Rhilius TechPad Pro 13/rhiliustechpadpro13_1.jpg                |
| false      | 2     | Rhilius TechPad Pro 13 product image 2           | _references Rhilius TechPad Pro 13_     |      | static/seed_images/products/image/Rhilius TechPad Pro 13/rhiliustechpadpro13_2.jpg                |
| true       | 0     | Rhilius TechPad Mini 8 default product image     | _references Rhilius TechPad Mini 8_     |      | static/seed_images/products/image/Rhilius TechPad Mini 8/rhiliustechpadmini8_default.png          |
| false      | 1     | Rhilius TechPad Mini 8 product image 1           | _references Rhilius TechPad Mini 8_     |      | static/seed_images/products/image/Rhilius TechPad Mini 8/rhiliustechpadmini8_1.png                |
| false      | 2     | Rhilius TechPad Mini 8 product image 2           | _references Rhilius TechPad Mini 8_     |      | static/seed_images/products/image/Rhilius TechPad Mini 8/rhiliustechpadmini8_2.png                |
| true       | 0     | Rhilius SketchPad 12 default product image       | _references Rhilius SketchPad 12_       |      | static/seed_images/products/image/Rhilius SketchPad 12/rhiliussketchpad12_default.png             |
| false      | 1     | Rhilius SketchPad 12 product image 1             | _references Rhilius SketchPad 12_       |      | static/seed_images/products/image/Rhilius SketchPad 12/rhiliussketchpad12_1.png                   |
| false      | 2     | Rhilius SketchPad 12 product image 2             | _references Rhilius SketchPad 12_       |      | static/seed_images/products/image/Rhilius SketchPad 12/rhiliussketchpad12_2.png                   |
| true       | 0     | Rhilius StudyPad 11 default product image        | _references Rhilius StudyPad 11_        |      | static/seed_images/products/image/Rhilius StudyPad 11/rhiliusstudypad11_default.png               |
| false      | 1     | Rhilius StudyPad 11 product image 1              | _references Rhilius StudyPad 11_        |      | static/seed_images/products/image/Rhilius StudyPad 11/rhiliusstudypad11_1.png                     |
| false      | 2     | Rhilius StudyPad 11 product image 2              | _references Rhilius StudyPad 11_        |      | static/seed_images/products/image/Rhilius StudyPad 11/rhiliusstudypad11_2.png                     |
| true       | 0     | Rhilius GamePad X default product image          | _references Rhilius GamePad X_          |      | static/seed_images/products/image/Rhilius GamePad X/rhiliusgamepadx_default.png                   |
| false      | 1     | Rhilius GamePad X product image 1                | _references Rhilius GamePad X_          |      | static/seed_images/products/image/Rhilius GamePad X/rhiliusgamepadx_1.png                         |
| false      | 2     | Rhilius GamePad X product image 2                | _references Rhilius GamePad X_          |      | static/seed_images/products/image/Rhilius GamePad X/rhiliusgamepadx_2.png                         |
| true       | 0     | Rhilius AeroBook 13 default product image        | _references Rhilius AeroBook 13_        |      | static/seed_images/products/image/Rhilius AeroBook 13/rhiliusaerobook13_default.png               |
| false      | 1     | Rhilius AeroBook 13 product image 1              | _references Rhilius AeroBook 13_        |      | static/seed_images/products/image/Rhilius AeroBook 13/rhiliusaerobook13_1.png                     |
| false      | 2     | Rhilius AeroBook 13 product image 2              | _references Rhilius AeroBook 13_        |      | static/seed_images/products/image/Rhilius AeroBook 13/rhiliusaerobook13_2.png                     |
| true       | 0     | Rhilius WorkBook 15 default product image        | _references Rhilius WorkBook 15_        |      | static/seed_images/products/image/Rhilius WorkBook 15/rhiliusworkbook15_default.png               |
| false      | 1     | Rhilius WorkBook 15 product image 1              | _references Rhilius WorkBook 15_        |      | static/seed_images/products/image/Rhilius WorkBook 15/rhiliusworkbook15_1.png                     |
| false      | 2     | Rhilius WorkBook 15 product image 2              | _references Rhilius WorkBook 15_        |      | static/seed_images/products/image/Rhilius WorkBook 15/rhiliusworkbook15_2.png                     |
| true       | 0     | Rhilius CreatorBook Pro 16 default product image | _references Rhilius CreatorBook Pro 16_ |      | static/seed_images/products/image/Rhilius CreatorBook Pro 16/rhiliuscreatorbookpro16_default.png  |
| false      | 1     | Rhilius CreatorBook Pro 16 product image 1       | _references Rhilius CreatorBook Pro 16_ |      | static/seed_images/products/image/Rhilius CreatorBook Pro 16/rhiliuscreatorbookpro16_1.png        |
| false      | 2     | Rhilius CreatorBook Pro 16 product image 2       | _references Rhilius CreatorBook Pro 16_ |      | static/seed_images/products/image/Rhilius CreatorBook Pro 16/rhiliuscreatorbookpro16_2.png        |
| true       | 0     | Rhilius GameBook RX 15 default product image     | _references Rhilius GameBook RX 15_     |      | static/seed_images/products/image/Rhilius GameBook RX 15/rhiliusgamebookrx15_default.png          |
| false      | 1     | Rhilius GameBook RX 15 product image 1           | _references Rhilius GameBook RX 15_     |      | static/seed_images/products/image/Rhilius GameBook RX 15/rhiliusgamebookrx15_1.png                |
| false      | 2     | Rhilius GameBook RX 15 product image 2           | _references Rhilius GameBook RX 15_     |      | static/seed_images/products/image/Rhilius GameBook RX 15/rhiliusgamebookrx15_2.png                |
| true       | 0     | Rhilius FlexBook 14 default product image        | _references Rhilius FlexBook 14_        |      | static/seed_images/products/image/Rhilius FlexBook 14/rhiliusflexbook14_default.png               |
| false      | 1     | Rhilius FlexBook 14 product image 1              | _references Rhilius FlexBook 14_        |      | static/seed_images/products/image/Rhilius FlexBook 14/rhiliusflexbook14_1.png                     |
| false      | 2     | Rhilius FlexBook 14 product image 2              | _references Rhilius FlexBook 14_        |      | static/seed_images/products/image/Rhilius FlexBook 14/rhiliusflexbook14_2.png                     |
| true       | 0     | Rhilius CloudBook Lite default product image     | _references Rhilius CloudBook Lite_     |      | static/seed_images/products/image/Rhilius CloudBook Lite/rhiliuscloudbooklite_default.png         |
| false      | 1     | Rhilius CloudBook Lite product image 1           | _references Rhilius CloudBook Lite_     |      | static/seed_images/products/image/Rhilius CloudBook Lite/rhiliuscloudbooklite_1.png               |
| false      | 2     | Rhilius CloudBook Lite product image 2           | _references Rhilius CloudBook Lite_     |      | static/seed_images/products/image/Rhilius CloudBook Lite/rhiliuscloudbooklite_2.png               |
| true       | 0     | Rhilius CoreBox Mini default product image       | _references Rhilius CoreBox Mini_       |      | static/seed_images/products/image/Rhilius CoreBox Mini/rhiliuscoreboxmini_default.png             |
| false      | 1     | Rhilius CoreBox Mini product image 1             | _references Rhilius CoreBox Mini_       |      | static/seed_images/products/image/Rhilius CoreBox Mini/rhiliuscoreboxmini_1.png                   |
| false      | 2     | Rhilius CoreBox Mini product image 2             | _references Rhilius CoreBox Mini_       |      | static/seed_images/products/image/Rhilius CoreBox Mini/rhiliuscoreboxmini_2.png                   |
| true       | 0     | Rhilius VisionDesk 24 default product image      | _references Rhilius VisionDesk 24_      |      | static/seed_images/products/image/Rhilius VisionDesk 24/rhiliusvisiondesk24_default.png           |
| false      | 1     | Rhilius VisionDesk 24 product image 1            | _references Rhilius VisionDesk 24_      |      | static/seed_images/products/image/Rhilius VisionDesk 24/rhiliusvisiondesk24_1.png                 |
| false      | 2     | Rhilius VisionDesk 24 product image 2            | _references Rhilius VisionDesk 24_      |      | static/seed_images/products/image/Rhilius VisionDesk 24/rhiliusvisiondesk24_2.png                 |
| true       | 0     | Rhilius TitanTower X default product image       | _references Rhilius TitanTower X_       |      | static/seed_images/products/image/Rhilius TitanTower X/rhiliustitantowerx_default.png             |
| false      | 1     | Rhilius TitanTower X product image 1             | _references Rhilius TitanTower X_       |      | static/seed_images/products/image/Rhilius TitanTower X/rhiliustitantowerx_1.png                   |
| false      | 2     | Rhilius TitanTower X product image 2             | _references Rhilius TitanTower X_       |      | static/seed_images/products/image/Rhilius TitanTower X/rhiliustitantowerx_2.png                   |
| true       | 0     | Rhilius CreatorStation Pro default product image | _references Rhilius CreatorStation Pro_ |      | static/seed_images/products/image/Rhilius CreatorStation Pro/rhiliuscreatorstationpro_default.png |
| false      | 1     | Rhilius CreatorStation Pro product image 1       | _references Rhilius CreatorStation Pro_ |      | static/seed_images/products/image/Rhilius CreatorStation Pro/rhiliuscreatorstationpro_1.png       |
| false      | 2     | Rhilius CreatorStation Pro product image 2       | _references Rhilius CreatorStation Pro_ |      | static/seed_images/products/image/Rhilius CreatorStation Pro/rhiliuscreatorstationpro_2.png       |
| true       | 0     | Rhilius GameStation R7 default product image     | _references Rhilius GameStation R7_     |      | static/seed_images/products/image/Rhilius GameStation R7/rhiliusgamestationr7_default.png         |
| false      | 1     | Rhilius GameStation R7 product image 1           | _references Rhilius GameStation R7_     |      | static/seed_images/products/image/Rhilius GameStation R7/rhiliusgamestationr7_1.png               |
| false      | 2     | Rhilius GameStation R7 product image 2           | _references Rhilius GameStation R7_     |      | static/seed_images/products/image/Rhilius GameStation R7/rhiliusgamestationr7_2.png               |
| true       | 0     | Rhilius OfficeBox S default product image        | _references Rhilius OfficeBox S_        |      | static/seed_images/products/image/Rhilius OfficeBox S/rhiliusofficeboxs_default.png               |
| false      | 1     | Rhilius OfficeBox S product image 1              | _references Rhilius OfficeBox S_        |      | static/seed_images/products/image/Rhilius OfficeBox S/rhiliusofficeboxs_1.png                     |
| false      | 2     | Rhilius OfficeBox S product image 2              | _references Rhilius OfficeBox S_        |      | static/seed_images/products/image/Rhilius OfficeBox S/rhiliusofficeboxs_2.png                     |
| false      | 3     | Rhilius OfficeBox S product image 3              | _references Rhilius OfficeBox S_        |      | static/seed_images/products/image/Rhilius OfficeBox S/rhiliusofficeboxs_3.png                     |

## Full development reset

For disposable environments, see [reset_dev](reset-dev.md) to rebuild the schema and clear uploaded media before migrating, importing taxonomy and seeding.
