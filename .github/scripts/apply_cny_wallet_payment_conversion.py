#!/usr/bin/env python3
"""Apply the CNY-base wallet conversion correction to the assembled R7 source tree."""

from pathlib import Path

ROOT = Path.cwd()


def replace_function(path: Path, start: str, end: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    start_at = source.find(start)
    if start_at < 0:
        raise SystemExit(f"{path}: function anchor not found: {start}")
    end_at = source.find(end, start_at)
    if end_at < 0:
        raise SystemExit(f"{path}: following function anchor not found: {end}")
    if replacement.strip() in source[start_at:end_at]:
        return
    source = source[:start_at] + replacement.rstrip() + "\n\n" + source[end_at:]
    path.write_text(source, encoding="utf-8")


topup = ROOT / "controller/topup.go"
replace_function(
    topup,
    "func getPayMoney(amount int64, group string) float64 {",
    "func getMinTopup() int64 {",
    """func getPayMoney(amount int64, group string) float64 {
	dAmount := decimal.NewFromInt(amount)
	switch operation_setting.GetQuotaDisplayType() {
	case operation_setting.QuotaDisplayTypeCNY:
		// The requested amount is already denominated in CNY. Applying the
		// legacy USD-to-CNY Price multiplier here would charge it twice.
	case operation_setting.QuotaDisplayTypeTokens:
		dQuotaPerUnit := decimal.NewFromFloat(common.QuotaPerUnit)
		if !dQuotaPerUnit.IsPositive() {
			return 0
		}
		dAmount = dAmount.Div(dQuotaPerUnit)
		dAmount = dAmount.Mul(decimal.NewFromFloat(operation_setting.Price))
	default:
		// USD and legacy custom display amounts are converted to the local
		// EPay currency using the configured system-USD payment price.
		dAmount = dAmount.Mul(decimal.NewFromFloat(operation_setting.Price))
	}

	topupGroupRatio := common.GetTopupGroupRatio(group)
	if topupGroupRatio == 0 {
		topupGroupRatio = 1
	}

	dTopupGroupRatio := decimal.NewFromFloat(topupGroupRatio)
	discount := 1.0
	if ds, ok := operation_setting.GetPaymentSetting().AmountDiscount[int(amount)]; ok && ds > 0 {
		discount = ds
	}
	dDiscount := decimal.NewFromFloat(discount)
	payMoney := dAmount.Mul(dTopupGroupRatio).Mul(dDiscount)
	return payMoney.InexactFloat64()
}""",
)

nowpayments = ROOT / "controller/nowpayments.go"
replace_function(
    nowpayments,
    "func getNowPaymentsUSDPrice(amount int64, group string) (decimal.Decimal, error) {",
    "func RequestNowPaymentsAmount(c *gin.Context) {",
    """func getNowPaymentsUSDPrice(amount int64, group string) (decimal.Decimal, error) {
	if amount <= 0 {
		return decimal.Zero, model.ErrPaymentAmountMismatch
	}
	usdAmount := decimal.NewFromInt(amount)
	switch operation_setting.GetQuotaDisplayType() {
	case operation_setting.QuotaDisplayTypeCNY:
		// CNY is the wallet base currency. Convert CNY to the provider's
		// USD-denominated quote using the configured locked display FX rate.
		exchangeRate := decimal.NewFromFloat(operation_setting.USDExchangeRate)
		if !exchangeRate.IsPositive() {
			return decimal.Zero, model.ErrPaymentAmountMismatch
		}
		usdAmount = usdAmount.Div(exchangeRate)
	case operation_setting.QuotaDisplayTypeTokens:
		quotaPerUnit := decimal.NewFromFloat(common.QuotaPerUnit)
		if !quotaPerUnit.IsPositive() {
			return decimal.Zero, model.ErrPaymentAmountMismatch
		}
		usdAmount = usdAmount.Div(quotaPerUnit)
	case operation_setting.QuotaDisplayTypeCustom:
		exchangeRate := decimal.NewFromFloat(operation_setting.GetGeneralSetting().CustomCurrencyExchangeRate)
		if !exchangeRate.IsPositive() {
			return decimal.Zero, model.ErrPaymentAmountMismatch
		}
		usdAmount = usdAmount.Div(exchangeRate)
	}

	topupGroupRatio := common.GetTopupGroupRatio(group)
	if topupGroupRatio == 0 {
		topupGroupRatio = 1
	}
	discount := 1.0
	if ds, ok := operation_setting.GetPaymentSetting().AmountDiscount[int(amount)]; ok && ds > 0 {
		discount = ds
	}
	usdAmount = usdAmount.Mul(decimal.NewFromFloat(topupGroupRatio)).Mul(decimal.NewFromFloat(discount)).Round(2)
	if !usdAmount.IsPositive() {
		return decimal.Zero, model.ErrPaymentAmountMismatch
	}
	return usdAmount, nil
}""",
)

test = ROOT / "controller/nowpayments_test.go"
test_source = test.read_text(encoding="utf-8")
test_source = test_source.replace(
    "func TestGetNowPaymentsUSDPriceReversesPaymentPriceRatio(t *testing.T) {",
    "func TestGetNowPaymentsUSDPriceUsesWalletCurrencyExchangeRate(t *testing.T) {",
)
old = 'assert.Equal(t, "10", cnyPrice.String(), "display FX must not alter the USD-denominated provider quote")'
new = 'assert.Equal(t, "1.1", cnyPrice.String(), "CNY wallet credit must be converted to USD using USDExchangeRate")'
if old not in test_source and new not in test_source:
    raise SystemExit("nowpayments_test.go: CNY expectation anchor not found")
test_source = test_source.replace(old, new)
test.write_text(test_source, encoding="utf-8")

conversion_test = ROOT / "controller/topup_cny_payment_conversion_test.go"
conversion_test.write_text(
    """package controller

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/setting/operation_setting"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestGetPayMoneyUsesCNYAsWalletBaseCurrency(t *testing.T) {
	general := operation_setting.GetGeneralSetting()
	oldDisplayType := general.QuotaDisplayType
	oldPrice := operation_setting.Price
	oldQuotaPerUnit := common.QuotaPerUnit
	oldTopupGroupRatio := common.TopupGroupRatio2JSONString()
	paymentSettings := operation_setting.GetPaymentSetting()
	oldAmountDiscount := paymentSettings.AmountDiscount
	t.Cleanup(func() {
		general.QuotaDisplayType = oldDisplayType
		operation_setting.Price = oldPrice
		common.QuotaPerUnit = oldQuotaPerUnit
		_ = common.UpdateTopupGroupRatioByJSONString(oldTopupGroupRatio)
		paymentSettings.AmountDiscount = oldAmountDiscount
	})

	operation_setting.Price = 7.3
	common.QuotaPerUnit = 500
	require.NoError(t, common.UpdateTopupGroupRatioByJSONString(`{"default":1}`))
	paymentSettings.AmountDiscount = map[int]float64{}

	general.QuotaDisplayType = operation_setting.QuotaDisplayTypeCNY
	assert.InDelta(t, 100.0, getPayMoney(100, "default"), 0.000001,
		"CNY input is already local currency and must not be multiplied by the legacy USD price")

	general.QuotaDisplayType = operation_setting.QuotaDisplayTypeUSD
	assert.InDelta(t, 730.0, getPayMoney(100, "default"), 0.000001,
		"USD input still converts to local CNY using Price")

	general.QuotaDisplayType = operation_setting.QuotaDisplayTypeTokens
	assert.InDelta(t, 7.3, getPayMoney(500, "default"), 0.000001,
		"token input first converts to one system USD and then to local CNY")
}
""",
    encoding="utf-8",
)

print("PASS: CNY EPay and NOWPayments conversion functions patched; targeted regression tests updated.")
