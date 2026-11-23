#!/usr/bin/env python3
"""Add opt-in end-to-end encrypted cross-device chat history sync to the assembled API.

The two sync endpoints are explicitly session-only in the personal-access-token route registry."""

from pathlib import Path
import re

ROOT = Path.cwd()


def insert_once(path: Path, marker: str, addition: str, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    if addition.strip() in source:
        return
    index = source.find(marker)
    if index < 0:
        raise SystemExit(f"{path}: insertion anchor missing for {label}")
    source = source[:index] + addition + source[index:]
    path.write_text(source, encoding="utf-8")


dto = ROOT / "relaykit/dto/user_settings.go"
dto_source = dto.read_text(encoding="utf-8")
if not re.search(r"\bChatHistorySync\s+string\b", dto_source):
    marker = '\tLanguage                         string  `json:"language,omitempty"`                             // Language 用户语言偏好 (zh, en)\n'
    if marker not in dto_source:
        raise SystemExit("relaykit/dto/user_settings.go: Language field anchor missing")
    dto.write_text(dto_source.replace(
        marker,
        marker + '\tChatHistorySync                  string  `json:"chat_history_sync,omitempty"`                  // Client-side encrypted cross-device chat history blob\n',
    ), encoding="utf-8")

model = ROOT / "model/user.go"
model_addition = """// UpdateUserChatHistorySync updates only the encrypted chat-sync field while
// holding the user row lock, preserving concurrent changes to other settings.
func UpdateUserChatHistorySync(userId int, payload string) error {
	if userId <= 0 {
		return common.NewMessage("ID is empty!")
	}
	err := DB.Transaction(func(tx *gorm.DB) error {
		var user User
		if err := lockForUpdate(tx).Select("id", "setting").First(&user, userId).Error; err != nil {
			return err
		}
		settings := user.GetSetting()
		settings.ChatHistorySync = payload
		settingBytes, err := common.Marshal(settings)
		if err != nil {
			return err
		}
		return tx.Model(&User{}).Where("id = ?", userId).Update("setting", string(settingBytes)).Error
	})
	if err != nil {
		return err
	}
	return PublishUserAuthCache(userId)
}

"""
insert_once(model, "// userBindColumns", model_addition, "model method")

router = ROOT / "router/api-router.go"
router_source = router.read_text(encoding="utf-8")
route_anchor = '\t\t\t\tselfRoute.PUT("/setting", controller.UpdateUserSetting)\n'
route_addition = (
    '\t\t\t\tselfRoute.GET("/chat-history-sync", controller.GetChatHistorySync)\n'
    '\t\t\t\tselfRoute.PUT("/chat-history-sync", middleware.UserCriticalRateLimit("chat-history-sync"), controller.UpdateChatHistorySync)\n'
)
if "selfRoute.GET(\"/chat-history-sync\"" not in router_source:
    if route_anchor not in router_source:
        raise SystemExit("router/api-router.go: self setting route anchor missing")
    router.write_text(router_source.replace(route_anchor, route_anchor + route_addition), encoding="utf-8")

controller = ROOT / "controller/chat_history_sync.go"
controller.write_text(
    """package controller

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
)

const maxChatHistorySyncCiphertextBytes = 2 * 1024 * 1024

type ChatHistorySyncPayload struct {
	Version    int    `json:"version"`
	KDF        string `json:"kdf"`
	Salt       string `json:"salt"`
	Nonce      string `json:"nonce"`
	Ciphertext string `json:"ciphertext"`
}

func validateChatHistorySyncPayload(payload ChatHistorySyncPayload) error {
	if payload.Version != 1 || payload.KDF != "PBKDF2-HMAC-SHA256" {
		return errors.New("unsupported encrypted chat sync format")
	}
	salt, err := base64.StdEncoding.DecodeString(payload.Salt)
	if err != nil || len(salt) != 16 {
		return errors.New("chat sync salt must be 16 bytes")
	}
	nonce, err := base64.StdEncoding.DecodeString(payload.Nonce)
	if err != nil || len(nonce) != 12 {
		return errors.New("chat sync nonce must be 12 bytes")
	}
	ciphertext, err := base64.StdEncoding.DecodeString(payload.Ciphertext)
	if err != nil || len(ciphertext) < 16 || len(ciphertext) > maxChatHistorySyncCiphertextBytes {
		return errors.New("chat sync ciphertext size is invalid")
	}
	return nil
}

func GetChatHistorySync(c *gin.Context) {
	settings, err := model.GetUserSetting(c.GetInt("id"), true)
	if err != nil {
		common.ApiError(c, err)
		return
	}
	var payload any
	if settings.ChatHistorySync != "" {
		var stored ChatHistorySyncPayload
		if err := json.Unmarshal([]byte(settings.ChatHistorySync), &stored); err != nil {
			common.ApiErrorT(c, "Stored chat sync payload is invalid")
			return
		}
		payload = stored
	}
	common.ApiSuccess(c, gin.H{"payload": payload})
}

func UpdateChatHistorySync(c *gin.Context) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxChatHistorySyncCiphertextBytes*2+8192)
	var req struct {
		Payload ChatHistorySyncPayload `json:"payload"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		common.ApiErrorT(c, "Invalid encrypted chat sync payload")
		return
	}
	if err := validateChatHistorySyncPayload(req.Payload); err != nil {
		common.ApiErrorT(c, err.Error())
		return
	}
	payloadBytes, err := common.Marshal(req.Payload)
	if err != nil {
		common.ApiError(c, err)
		return
	}
	if err := model.UpdateUserChatHistorySync(c.GetInt("id"), string(payloadBytes)); err != nil {
		common.ApiError(c, err)
		return
	}
	common.ApiSuccess(c, gin.H{"updated_at": common.GetTimestamp()})
}
""",
    encoding="utf-8",
)

test = ROOT / "controller/chat_history_sync_test.go"
test.write_text(
    """package controller

import (
	"encoding/base64"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func validChatHistorySyncPayload() ChatHistorySyncPayload {
	return ChatHistorySyncPayload{
		Version:    1,
		KDF:        "PBKDF2-HMAC-SHA256",
		Salt:       base64.StdEncoding.EncodeToString(make([]byte, 16)),
		Nonce:      base64.StdEncoding.EncodeToString(make([]byte, 12)),
		Ciphertext: base64.StdEncoding.EncodeToString(make([]byte, 32)),
	}
}

func TestChatHistorySyncPayloadValidation(t *testing.T) {
	valid := validChatHistorySyncPayload()
	require.NoError(t, validateChatHistorySyncPayload(valid))

	tests := []struct {
		name    string
		mutate  func(*ChatHistorySyncPayload)
	}{
		{name: "unsupported version", mutate: func(p *ChatHistorySyncPayload) { p.Version = 2 }},
		{name: "unsupported kdf", mutate: func(p *ChatHistorySyncPayload) { p.KDF = "plain-password" }},
		{name: "short salt", mutate: func(p *ChatHistorySyncPayload) { p.Salt = base64.StdEncoding.EncodeToString(make([]byte, 8)) }},
		{name: "short nonce", mutate: func(p *ChatHistorySyncPayload) { p.Nonce = base64.StdEncoding.EncodeToString(make([]byte, 8)) }},
		{name: "missing gcm tag", mutate: func(p *ChatHistorySyncPayload) { p.Ciphertext = base64.StdEncoding.EncodeToString(make([]byte, 8)) }},
		{name: "invalid ciphertext base64", mutate: func(p *ChatHistorySyncPayload) { p.Ciphertext = "%%%" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			payload := valid
			test.mutate(&payload)
			assert.Error(t, validateChatHistorySyncPayload(payload))
		})
	}
}
""",
    encoding="utf-8",
)

# The encrypted blob is a browser-session feature, not a personal API-token
# capability. Explicitly classify both endpoints as session-only so PATs cannot
# read or overwrite a user's cross-device encrypted history.
access_rules = ROOT / "middleware/access_token_routes.go"
access_rule_additions = (
    '\t"GET /api/user/chat-history-sync": accessTokenSessionRule,\n'
    '\t"PUT /api/user/chat-history-sync": accessTokenSessionRule,\n'
)
rules_source = access_rules.read_text(encoding="utf-8")
for line in access_rule_additions.splitlines():
    if line.strip() not in rules_source:
        anchor = '\t"DELETE /api/user/self":                        accessTokenScopeRule("account_security:write"),\n'
        if anchor not in rules_source:
            raise SystemExit("middleware/access_token_routes.go: user self route anchor missing")
        rules_source = rules_source.replace(anchor, anchor + line + "\n", 1)
access_rules.write_text(rules_source, encoding="utf-8")
for line in access_rule_additions.splitlines():
    if rules_source.count(line.strip()) != 1:
        raise SystemExit(f"middleware/access_token_routes.go: expected exactly one session rule: {line.strip()}")

print("PASS: encrypted chat sync DTO, row-locked setting update, authenticated endpoints and payload validation tests applied.")
