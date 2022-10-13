from decimal import Decimal
from unittest.mock import Mock, patch

import graphene
import pytest
from prices import Money, TaxedMoney

from ....product.models import ProductVariant, ProductVariantChannelListing
from ....product.utils.availability import get_variant_availability
from ....tax import TaxCalculationStrategy
from ....tax.models import TaxClassCountryRate, TaxConfigurationPerCountry
from ...tests.utils import get_graphql_content

QUERY_GET_VARIANT_PRICING = """
fragment VariantPricingInfo on VariantPricingInfo {
  onSale
  discount {
    currency
    net {
      amount
    }
  }
  priceUndiscounted {
    currency
    net {
      amount
    }
  }
  price {
    currency
    net {
      amount
    }
  }
}
query ($channel: String, $address: AddressInput) {
  products(first: 1, channel: $channel) {
    edges {
      node {
        variants {
          pricing(address: $address) {
            ...VariantPricingInfo
          }
          pricingNoAddress: pricing {
            ...VariantPricingInfo
          }
        }
      }
    }
  }
}
"""


def test_get_variant_pricing_on_sale(api_client, sale, product, channel_USD):
    price = product.variants.first().channel_listings.get().price
    sale_discounted_value = sale.channel_listings.get().discount_value
    discounted_price = price.amount - sale_discounted_value

    variables = {"channel": channel_USD.slug, "address": {"country": "US"}}
    response = api_client.post_graphql(QUERY_GET_VARIANT_PRICING, variables)
    content = get_graphql_content(response)

    pricing = content["data"]["products"]["edges"][0]["node"]["variants"][0]["pricing"]

    # ensure the availability was correctly retrieved and sent
    assert pricing

    # check availability
    assert pricing["onSale"] is True

    # check the discount
    assert pricing["discount"]["currency"] == price.currency
    assert pricing["discount"]["net"]["amount"] == discounted_price

    # check the undiscounted price
    assert pricing["priceUndiscounted"]["currency"] == price.currency
    assert pricing["priceUndiscounted"]["net"]["amount"] == price.amount

    # check the discounted price
    assert pricing["price"]["currency"] == price.currency
    assert pricing["price"]["net"]["amount"] == discounted_price


def test_get_variant_pricing_not_on_sale(api_client, product, channel_USD):
    price = product.variants.first().channel_listings.get().price

    variables = {"channel": channel_USD.slug, "address": {"country": "US"}}
    response = api_client.post_graphql(QUERY_GET_VARIANT_PRICING, variables)
    content = get_graphql_content(response)

    pricing = content["data"]["products"]["edges"][0]["node"]["variants"][0]["pricing"]

    # ensure the availability was correctly retrieved and sent
    assert pricing

    # check availability
    assert pricing["onSale"] is False

    # check the discount
    assert pricing["discount"] is None

    # check the undiscounted price
    assert pricing["priceUndiscounted"]["currency"] == price.currency
    assert pricing["priceUndiscounted"]["net"]["amount"] == price.amount

    # check the discounted price
    assert pricing["price"]["currency"] == price.currency
    assert pricing["price"]["net"]["amount"] == price.amount


def test_variant_pricing(
    variant: ProductVariant, monkeypatch, settings, stock, channel_USD
):
    product = variant.product
    tax_class = product.tax_class or product.product_type.tax_class

    tc = channel_USD.tax_configuration
    tc.tax_calculation_strategy = TaxCalculationStrategy.FLAT_RATES
    tc.charge_taxes = True
    tc.prices_entered_with_tax = False
    tc.save()

    tax_rate = Decimal(23)
    country = "PL"
    tax_class.country_rates.update_or_create(rate=tax_rate, country=country)

    taxed_price = TaxedMoney(Money("10.0", "USD"), Money("12.30", "USD"))
    product_channel_listing = product.channel_listings.get()
    variant_channel_listing = variant.channel_listings.get()

    pricing = get_variant_availability(
        variant=variant,
        variant_channel_listing=variant_channel_listing,
        product=product,
        product_channel_listing=product_channel_listing,
        collections=[],
        discounts=[],
        channel=channel_USD,
        tax_rate=tax_rate,
        tax_calculation_strategy=tc.tax_calculation_strategy,
        prices_entered_with_tax=tc.prices_entered_with_tax,
    )
    assert pricing.price == taxed_price
    assert pricing.price_local_currency is None

    monkeypatch.setattr(
        "django_prices_openexchangerates.models.get_rates",
        lambda c: {"PLN": Mock(rate=2)},
    )

    settings.OPENEXCHANGERATES_API_KEY = "fake-key"

    pricing = get_variant_availability(
        variant=variant,
        variant_channel_listing=variant_channel_listing,
        product=product,
        product_channel_listing=product_channel_listing,
        collections=[],
        discounts=[],
        channel=channel_USD,
        local_currency="PLN",
        tax_rate=tax_rate,
        tax_calculation_strategy=tc.tax_calculation_strategy,
        prices_entered_with_tax=tc.prices_entered_with_tax,
    )
    assert pricing.price_local_currency.currency == "PLN"  # type: ignore

    pricing = get_variant_availability(
        variant=variant,
        variant_channel_listing=variant_channel_listing,
        product=product,
        product_channel_listing=product_channel_listing,
        collections=[],
        discounts=[],
        channel=channel_USD,
        tax_rate=tax_rate,
        tax_calculation_strategy=tc.tax_calculation_strategy,
        prices_entered_with_tax=tc.prices_entered_with_tax,
    )
    assert pricing.price.tax.amount
    assert pricing.price_undiscounted.tax.amount
    assert pricing.price_undiscounted.tax.amount


QUERY_GET_PRODUCT_VARIANTS_PRICING = """
    query getProductVariants($id: ID!, $channel: String, $address: AddressInput) {
        product(id: $id, channel: $channel) {
            variants {
                id
                pricingNoAddress: pricing {
                    priceUndiscounted {
                        gross {
                            amount
                        }
                    }
                }
                pricing(address: $address) {
                    priceUndiscounted {
                        gross {
                            amount
                        }
                    }
                }
            }
        }
    }
"""


@pytest.mark.parametrize(
    "variant_price_amount, api_variant_price",
    [(200, 200), (0, 0)],
)
def test_product_variant_price(
    variant_price_amount,
    api_variant_price,
    user_api_client,
    variant,
    stock,
    channel_USD,
):
    product = variant.product
    ProductVariantChannelListing.objects.filter(
        channel=channel_USD, variant__product_id=product.pk
    ).update(price_amount=variant_price_amount)

    product_id = graphene.Node.to_global_id("Product", variant.product.id)
    variables = {
        "id": product_id,
        "channel": channel_USD.slug,
        "address": {"country": "US"},
    }
    response = user_api_client.post_graphql(
        QUERY_GET_PRODUCT_VARIANTS_PRICING, variables
    )
    content = get_graphql_content(response)
    data = content["data"]["product"]
    variant_price = data["variants"][0]["pricing"]["priceUndiscounted"]["gross"]
    assert variant_price["amount"] == api_variant_price


def test_product_variant_without_price_as_user(
    user_api_client,
    variant,
    stock,
    channel_USD,
):
    variant.channel_listings.filter(channel=channel_USD).update(price_amount=None)
    product_id = graphene.Node.to_global_id("Product", variant.product.id)
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.id)
    variables = {
        "id": product_id,
        "channel": channel_USD.slug,
        "address": {"country": "US"},
    }

    response = user_api_client.post_graphql(
        QUERY_GET_PRODUCT_VARIANTS_PRICING, variables
    )
    content = get_graphql_content(response)

    variants_data = content["data"]["product"]["variants"]
    assert not variants_data[0]["id"] == variant_id
    assert len(variants_data) == 1


def test_product_variant_without_price_as_staff_without_permission(
    staff_api_client,
    variant,
    stock,
    channel_USD,
):

    variant_channel_listing = variant.channel_listings.first()
    variant_channel_listing.price_amount = None
    variant_channel_listing.save()

    product_id = graphene.Node.to_global_id("Product", variant.product.id)
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.id)
    variables = {
        "id": product_id,
        "channel": channel_USD.slug,
        "address": {"country": "US"},
    }
    response = staff_api_client.post_graphql(
        QUERY_GET_PRODUCT_VARIANTS_PRICING, variables
    )
    content = get_graphql_content(response)
    variants_data = content["data"]["product"]["variants"]

    assert len(variants_data) == 1

    assert variants_data[0]["pricing"] is not None
    assert variants_data[0]["id"] != variant_id


def test_product_variant_without_price_as_staff_with_permission(
    staff_api_client, variant, stock, channel_USD, permission_manage_products
):

    variant_channel_listing = variant.channel_listings.first()
    variant_channel_listing.price_amount = None
    variant_channel_listing.save()

    product_id = graphene.Node.to_global_id("Product", variant.product.id)
    variant_id = graphene.Node.to_global_id("ProductVariant", variant.id)
    variables = {
        "id": product_id,
        "channel": channel_USD.slug,
        "address": {"country": "US"},
    }
    response = staff_api_client.post_graphql(
        QUERY_GET_PRODUCT_VARIANTS_PRICING,
        variables,
        permissions=[permission_manage_products],
        check_no_permissions=False,
    )
    content = get_graphql_content(response)
    variants_data = content["data"]["product"]["variants"]

    assert len(variants_data) == 2

    assert variants_data[0]["pricing"] is not None
    assert variants_data[1]["id"] == variant_id
    assert variants_data[1]["pricing"] is None


QUERY_GET_PRODUCT_VARIANTS_PRICING_NO_ADDRESS = """
    query getProductVariants($id: ID!, $channel: String) {
        product(id: $id, channel: $channel) {
            variants {
                id
                pricing {
                    priceUndiscounted {
                        gross {
                            amount
                        }
                    }
                }
            }
        }
    }
"""


@patch(
    "saleor.graphql.product.types.products.get_tax_rate_for_tax_class",
)
def test_product_variant_price_no_address(
    mock_get_tax_rate_for_tax_class, user_api_client, variant, stock, channel_USD
):
    channel_USD.default_country = "FR"
    channel_USD.save()
    product_id = graphene.Node.to_global_id("Product", variant.product.id)
    variables = {"id": product_id, "channel": channel_USD.slug}
    user_api_client.post_graphql(
        QUERY_GET_PRODUCT_VARIANTS_PRICING_NO_ADDRESS, variables
    )
    assert (
        mock_get_tax_rate_for_tax_class.call_args[0][2] == channel_USD.default_country
    )


FRAGMENT_PRICE = """
  fragment Price on TaxedMoney {
    gross {
      amount
    }
    net {
      amount
    }
    tax {
      amount
    }
  }
"""

FRAGMENT_PRICING = (
    """
  fragment Pricing on VariantPricingInfo {
    price {
      ...Price
    }
    priceUndiscounted {
      ...Price
    }
  }
"""
    + FRAGMENT_PRICE
)


QUERY_PRODUCT_VARIANT_PRICING = (
    """
  query Variant($id:ID!, $channel: String!) {
    productVariant(id: $id, channel: $channel) {
      pricingPL: pricing(address: { country: PL }) {
        ...Pricing
      }
      pricingDE: pricing(address: { country: DE }) {
        ...Pricing
      }
      pricing: pricing {
        ...Pricing
      }
    }
  }
"""
    + FRAGMENT_PRICING
)


def _enable_flat_rates(channel, prices_entered_with_tax):
    tc = channel.tax_configuration
    tc.tax_calculation_strategy = TaxCalculationStrategy.FLAT_RATES
    tc.prices_entered_with_tax = prices_entered_with_tax
    tc.charge_taxes = True
    tc.country_exceptions.all().delete()
    tc.country_exceptions.create(
        country="PL",
        charge_taxes=True,
        tax_calculation_strategy=TaxCalculationStrategy.FLAT_RATES,
    )
    tc.country_exceptions.create(
        country="DE",
        charge_taxes=True,
        tax_calculation_strategy=TaxCalculationStrategy.FLAT_RATES,
    )
    tc.save()


def _configure_tax_rates(product):
    product.tax_class.country_rates.all().delete()
    product.tax_class.country_rates.create(country="PL", rate=23)
    product.tax_class.country_rates.create(country="DE", rate=19)


@pytest.mark.parametrize(
    "net_PL, gross_PL, net_DE, gross_DE, prices_entered_with_tax",
    [
        (40.65, 50.00, 42.02, 50.00, True),
        (50.00, 61.50, 50.00, 59.50, False),
    ],
)
def test_product_variant_pricing(
    product_available_in_many_channels,
    channel_PLN,
    user_api_client,
    net_PL,
    gross_PL,
    net_DE,
    gross_DE,
    prices_entered_with_tax,
):
    # given
    product = product_available_in_many_channels
    variant = product.variants.first()
    _enable_flat_rates(channel_PLN, prices_entered_with_tax)
    _configure_tax_rates(product)

    # when
    variables = {
        "id": graphene.Node.to_global_id("ProductVariant", variant.id),
        "channel": channel_PLN.slug,
    }
    response = user_api_client.post_graphql(QUERY_PRODUCT_VARIANT_PRICING, variables)
    content = get_graphql_content(response)
    data = content["data"]["productVariant"]

    # then
    price_PL = data["pricingPL"]["price"]
    price_undiscounted_PL = data["pricingPL"]["priceUndiscounted"]
    assert price_PL["net"]["amount"] == net_PL
    assert price_PL["gross"]["amount"] == gross_PL
    assert price_undiscounted_PL["net"]["amount"] == net_PL
    assert price_undiscounted_PL["gross"]["amount"] == gross_PL

    price_DE = data["pricingDE"]["price"]
    price_undiscounted_DE = data["pricingDE"]["priceUndiscounted"]
    assert price_DE["net"]["amount"] == net_DE
    assert price_DE["gross"]["amount"] == gross_DE
    assert price_undiscounted_DE["net"]["amount"] == net_DE
    assert price_undiscounted_DE["gross"]["amount"] == gross_DE


def test_product_variant_pricing_default_country_default_rate(
    product_available_in_many_channels,
    channel_PLN,
    user_api_client,
):
    # given
    product = product_available_in_many_channels
    variant = product.variants.first()
    _enable_flat_rates(channel_PLN, True)
    TaxClassCountryRate.objects.all().delete()
    TaxClassCountryRate.objects.create(country=channel_PLN.default_country, rate=23)

    # when
    variables = {
        "id": graphene.Node.to_global_id("ProductVariant", variant.id),
        "channel": channel_PLN.slug,
    }
    response = user_api_client.post_graphql(QUERY_PRODUCT_VARIANT_PRICING, variables)
    content = get_graphql_content(response)
    data = content["data"]["productVariant"]

    # then
    price_PL = data["pricingPL"]["price"]
    price_undiscounted_PL = data["pricingPL"]["priceUndiscounted"]
    assert price_PL["net"]["amount"] == 40.65
    assert price_PL["gross"]["amount"] == 50.00
    assert price_undiscounted_PL["net"]["amount"] == 40.65
    assert price_undiscounted_PL["gross"]["amount"] == 50.00


def test_product_variant_pricing_use_tax_class_from_product_type(
    product_available_in_many_channels,
    channel_PLN,
    user_api_client,
):
    # given
    product = product_available_in_many_channels
    variant = product.variants.first()
    _enable_flat_rates(channel_PLN, True)
    TaxClassCountryRate.objects.all().delete()
    product.tax_class = None
    product.save(update_fields=["tax_class"])
    product.product_type.tax_class.country_rates.create(
        country=channel_PLN.default_country, rate=23
    )

    # when
    variables = {
        "id": graphene.Node.to_global_id("ProductVariant", variant.id),
        "channel": channel_PLN.slug,
    }
    response = user_api_client.post_graphql(QUERY_PRODUCT_VARIANT_PRICING, variables)
    content = get_graphql_content(response)
    data = content["data"]["productVariant"]

    # then
    price_PL = data["pricingPL"]["price"]
    price_undiscounted_PL = data["pricingPL"]["priceUndiscounted"]
    assert price_PL["net"]["amount"] == 40.65
    assert price_PL["gross"]["amount"] == 50.00
    assert price_undiscounted_PL["net"]["amount"] == 40.65
    assert price_undiscounted_PL["gross"]["amount"] == 50.00


def test_product_variant_pricing_no_flat_rates_in_one_country(
    product_available_in_many_channels,
    channel_PLN,
    user_api_client,
):
    # given
    product = product_available_in_many_channels
    variant = product.variants.first()
    _enable_flat_rates(channel_PLN, True)
    _configure_tax_rates(product)
    TaxConfigurationPerCountry.objects.filter(country="PL").update(
        tax_calculation_strategy=None
    )

    # when
    variables = {
        "id": graphene.Node.to_global_id("ProductVariant", variant.id),
        "channel": channel_PLN.slug,
    }
    response = user_api_client.post_graphql(QUERY_PRODUCT_VARIANT_PRICING, variables)
    content = get_graphql_content(response)
    data = content["data"]["productVariant"]

    # then
    price_PL = data["pricingPL"]["price"]
    price_undiscounted_PL = data["pricingPL"]["priceUndiscounted"]
    assert price_PL["net"]["amount"] == 50.00
    assert price_PL["gross"]["amount"] == 50.00
    assert price_undiscounted_PL["net"]["amount"] == 50.00
    assert price_undiscounted_PL["gross"]["amount"] == 50.00

    price_DE = data["pricingDE"]["price"]
    price_undiscounted_DE = data["pricingDE"]["priceUndiscounted"]
    assert price_DE["net"]["amount"] == 42.02
    assert price_DE["gross"]["amount"] == 50.00
    assert price_undiscounted_DE["net"]["amount"] == 42.02
    assert price_undiscounted_DE["gross"]["amount"] == 50.00
