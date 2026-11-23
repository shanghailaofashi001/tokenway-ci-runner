#!/usr/bin/env python3
"""Apply the TokenWay owner-scoped OpenAI Files API to the pinned upstream tree."""
from pathlib import Path

ROOT = Path.cwd()


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"{path}: expected insertion anchor missing ({label})")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def write_new(path: str, content: str) -> None:
    target = ROOT / path
    if target.exists():
        current = target.read_text(encoding="utf-8")
        if current != content:
            raise SystemExit(f"{path}: refusing to overwrite unexpected existing source")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


model = r'''package model

import (
    "errors"
    "time"

    "gorm.io/gorm"
)

type UserFile struct {
    ID        string    `json:"id" gorm:"primaryKey;size:64"`
    UserID    int       `json:"-" gorm:"not null;index:idx_user_files_owner_created,priority:1"`
    Object    string    `json:"object" gorm:"size:32;not null"`
    Filename  string    `json:"filename" gorm:"size:255;not null"`
    Purpose   string    `json:"purpose" gorm:"size:64;not null;index"`
    Bytes     int64     `json:"bytes" gorm:"not null"`
    MIMEType  string    `json:"-" gorm:"size:255"`
    StorageKey string   `json:"-" gorm:"size:512;not null;uniqueIndex"`
    CreatedAt int64     `json:"created_at" gorm:"not null;index:idx_user_files_owner_created,priority:2,sort:desc"`
}

func (UserFile) TableName() string { return "user_files" }

func CreateUserFile(file *UserFile) error {
    if file == nil || file.ID == "" || file.UserID <= 0 || file.StorageKey == "" {
        return errors.New("invalid user file metadata")
    }
    if file.CreatedAt == 0 {
        file.CreatedAt = time.Now().Unix()
    }
    if file.Object == "" {
        file.Object = "file"
    }
    return DB.Create(file).Error
}

func GetUserFile(userID int, id string) (*UserFile, error) {
    if userID <= 0 || id == "" {
        return nil, gorm.ErrRecordNotFound
    }
    var file UserFile
    if err := DB.Where("id = ? AND user_id = ?", id, userID).First(&file).Error; err != nil {
        return nil, err
    }
    return &file, nil
}

func ListUserFiles(userID int, limit int, after string) ([]UserFile, error) {
    if userID <= 0 {
        return nil, errors.New("invalid user id")
    }
    if limit < 1 || limit > 100 {
        limit = 20
    }
    query := DB.Where("user_id = ?", userID)
    if after != "" {
        cursor, err := GetUserFile(userID, after)
        if err != nil {
            return nil, err
        }
        query = query.Where("(created_at < ?) OR (created_at = ? AND id < ?)", cursor.CreatedAt, cursor.CreatedAt, cursor.ID)
    }
    var files []UserFile
    err := query.Order("created_at DESC").Order("id DESC").Limit(limit + 1).Find(&files).Error
    if len(files) > limit {
        files = files[:limit]
    }
    return files, err
}

func DeleteUserFile(userID int, id string) (*UserFile, error) {
    file, err := GetUserFile(userID, id)
    if err != nil {
        return nil, err
    }
    result := DB.Where("id = ? AND user_id = ?", id, userID).Delete(&UserFile{})
    if result.Error != nil {
        return nil, result.Error
    }
    if result.RowsAffected != 1 {
        return nil, gorm.ErrRecordNotFound
    }
    return file, nil
}
'''

controller = r'''package controller

import (
    "crypto/rand"
    "encoding/hex"
    "errors"
    "fmt"
    "io"
    "mime"
    "net/http"
    "os"
    "path/filepath"
    "strconv"
    "strings"
    "time"

    "github.com/QuantumNous/new-api/model"
    "github.com/gin-gonic/gin"
    "gorm.io/gorm"
)

const maxUserFileBytes int64 = 512 << 20

func userFileStorageRoot() (string, error) {
    root := strings.TrimSpace(os.Getenv("TOKENWAY_FILE_STORAGE_DIR"))
    if root == "" {
        root = filepath.Join("data", "files")
    }
    absolute, err := filepath.Abs(root)
    if err != nil {
        return "", err
    }
    if err := os.MkdirAll(absolute, 0700); err != nil {
        return "", err
    }
    return absolute, nil
}

func userFilePath(root string, userID int, id string) string {
    return filepath.Join(root, strconv.Itoa(userID), id[:2], id)
}

func newUserFileID() (string, error) {
    var value [16]byte
    if _, err := rand.Read(value[:]); err != nil {
        return "", err
    }
    return hex.EncodeToString(value[:]), nil
}

func userFileError(c *gin.Context, status int, message string) {
    c.JSON(status, gin.H{"error": gin.H{
        "message": message,
        "type": "invalid_request_error",
        "param": nil,
        "code": nil,
    }})
}

func userFileOwner(c *gin.Context) (int, bool) {
    id := c.GetInt("id")
    if id <= 0 {
        userFileError(c, http.StatusUnauthorized, "Authentication required")
        return 0, false
    }
    return id, true
}

func userFileResponse(file model.UserFile) gin.H {
    return gin.H{
        "id": file.ID,
        "object": "file",
        "bytes": file.Bytes,
        "created_at": file.CreatedAt,
        "filename": file.Filename,
        "purpose": file.Purpose,
        "status": "processed",
    }
}

// ListUserFiles implements GET /v1/files and never returns another user's rows.
func ListUserFiles(c *gin.Context) {
    userID, ok := userFileOwner(c)
    if !ok { return }
    limit := 20
    if raw := c.Query("limit"); raw != "" {
        parsed, err := strconv.Atoi(raw)
        if err != nil || parsed < 1 || parsed > 100 {
            userFileError(c, http.StatusBadRequest, "limit must be between 1 and 100")
            return
        }
        limit = parsed
    }
    files, err := model.ListUserFiles(userID, limit, c.Query("after"))
    if errors.Is(err, gorm.ErrRecordNotFound) {
        userFileError(c, http.StatusNotFound, "File cursor not found")
        return
    }
    if err != nil {
        userFileError(c, http.StatusInternalServerError, "Unable to list files")
        return
    }
    data := make([]gin.H, 0, len(files))
    for _, file := range files { data = append(data, userFileResponse(file)) }
    c.JSON(http.StatusOK, gin.H{"object": "list", "data": data, "has_more": false})
}

// UploadUserFile implements multipart/form-data POST /v1/files.
func UploadUserFile(c *gin.Context) {
    userID, ok := userFileOwner(c)
    if !ok { return }
    c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxUserFileBytes+(2<<20))
    if err := c.Request.ParseMultipartForm(32 << 20); err != nil {
        userFileError(c, http.StatusBadRequest, "A valid multipart upload is required")
        return
    }
    header, err := c.FormFile("file")
    if err != nil {
        userFileError(c, http.StatusBadRequest, "The file field is required")
        return
    }
    if header.Size > maxUserFileBytes {
        userFileError(c, http.StatusRequestEntityTooLarge, "File exceeds the 512 MiB limit")
        return
    }
    filename := filepath.Base(strings.TrimSpace(header.Filename))
    if filename == "" || filename == "." || len(filename) > 255 || strings.ContainsAny(filename, "\x00/\\") {
        userFileError(c, http.StatusBadRequest, "Invalid filename")
        return
    }
    purpose := strings.TrimSpace(c.PostForm("purpose"))
    if purpose == "" || len(purpose) > 64 {
        userFileError(c, http.StatusBadRequest, "A valid purpose is required")
        return
    }
    source, err := header.Open()
    if err != nil {
        userFileError(c, http.StatusBadRequest, "Unable to read uploaded file")
        return
    }
    defer source.Close()

    root, err := userFileStorageRoot()
    if err != nil {
        userFileError(c, http.StatusInternalServerError, "File storage is unavailable")
        return
    }
    id, err := newUserFileID()
    if err != nil {
        userFileError(c, http.StatusInternalServerError, "Unable to allocate file identifier")
        return
    }
    destination := userFilePath(root, userID, id)
    if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
        userFileError(c, http.StatusInternalServerError, "File storage is unavailable")
        return
    }
    temporary, err := os.CreateTemp(filepath.Dir(destination), ".upload-*")
    if err != nil {
        userFileError(c, http.StatusInternalServerError, "File storage is unavailable")
        return
    }
    temporaryName := temporary.Name()
    committed := false
    defer func() {
        _ = temporary.Close()
        if !committed { _ = os.Remove(temporaryName) }
    }()

    written, err := io.Copy(temporary, io.LimitReader(source, maxUserFileBytes+1))
    if err != nil {
        userFileError(c, http.StatusBadRequest, "Unable to store uploaded file")
        return
    }
    if written > maxUserFileBytes {
        userFileError(c, http.StatusRequestEntityTooLarge, "File exceeds the 512 MiB limit")
        return
    }
    if err := temporary.Sync(); err != nil {
        userFileError(c, http.StatusInternalServerError, "Unable to persist uploaded file")
        return
    }
    if err := temporary.Close(); err != nil {
        userFileError(c, http.StatusInternalServerError, "Unable to persist uploaded file")
        return
    }
    if err := os.Rename(temporaryName, destination); err != nil {
        userFileError(c, http.StatusInternalServerError, "Unable to finalize uploaded file")
        return
    }
    committed = true

    mimeType := "application/octet-stream"
    probe, err := os.Open(destination)
    if err == nil {
        var sample [512]byte
        n, _ := probe.Read(sample[:])
        _ = probe.Close()
        if n > 0 { mimeType = http.DetectContentType(sample[:n]) }
    }
    record := model.UserFile{
        ID: id, UserID: userID, Object: "file", Filename: filename,
        Purpose: purpose, Bytes: written, MIMEType: mimeType,
        StorageKey: filepath.ToSlash(filepath.Join(strconv.Itoa(userID), id[:2], id)),
        CreatedAt: time.Now().Unix(),
    }
    if err := model.CreateUserFile(&record); err != nil {
        _ = os.Remove(destination)
        userFileError(c, http.StatusInternalServerError, "Unable to save file metadata")
        return
    }
    c.JSON(http.StatusOK, userFileResponse(record))
}

// GetUserFileContent implements GET /v1/files/:id/content.
func GetUserFileContent(c *gin.Context) {
    userID, ok := userFileOwner(c)
    if !ok { return }
    record, err := model.GetUserFile(userID, c.Param("id"))
    if errors.Is(err, gorm.ErrRecordNotFound) {
        userFileError(c, http.StatusNotFound, "File not found")
        return
    }
    if err != nil {
        userFileError(c, http.StatusInternalServerError, "Unable to load file metadata")
        return
    }
    root, err := userFileStorageRoot()
    if err != nil {
        userFileError(c, http.StatusInternalServerError, "File storage is unavailable")
        return
    }
    path := userFilePath(root, userID, record.ID)
    file, err := os.Open(path)
    if err != nil {
        if errors.Is(err, os.ErrNotExist) { userFileError(c, http.StatusNotFound, "File content not found") } else { userFileError(c, http.StatusInternalServerError, "Unable to read file content") }
        return
    }
    defer file.Close()
    c.Header("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": record.Filename}))
    c.DataFromReader(http.StatusOK, record.Bytes, record.MIMEType, file, false)
}

// DeleteUserFile implements DELETE /v1/files/:id. Ownership is part of the delete predicate.
func DeleteUserFile(c *gin.Context) {
    userID, ok := userFileOwner(c)
    if !ok { return }
    record, err := model.DeleteUserFile(userID, c.Param("id"))
    if errors.Is(err, gorm.ErrRecordNotFound) {
        userFileError(c, http.StatusNotFound, "File not found")
        return
    }
    if err != nil {
        userFileError(c, http.StatusInternalServerError, "Unable to delete file")
        return
    }
    root, rootErr := userFileStorageRoot()
    if rootErr == nil {
        if removeErr := os.Remove(userFilePath(root, userID, record.ID)); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
            // Metadata is already deleted; leave an orphan for the maintenance cleanup rather than
            // returning a false failure that encourages a duplicate delete request.
            _ = fmt.Sprintf("orphaned file %s: %v", record.ID, removeErr)
        }
    }
    c.JSON(http.StatusOK, gin.H{"id": record.ID, "object": "file", "deleted": true})
}
'''

model_test = r'''package model

import (
    "testing"

    "github.com/stretchr/testify/require"
    "gorm.io/driver/sqlite"
    "gorm.io/gorm"
)

func TestUserFileOwnershipAndPagination(t *testing.T) {
    previous := DB
    database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
    require.NoError(t, err)
    DB = database
    t.Cleanup(func() { DB = previous; sqlDB, _ := database.DB(); _ = sqlDB.Close() })
    require.NoError(t, database.AutoMigrate(&UserFile{}))
    require.NoError(t, CreateUserFile(&UserFile{ID:"a1", UserID:1, Object:"file", Filename:"one.txt", Purpose:"user_data", Bytes:1, StorageKey:"1/a1"}))
    require.NoError(t, CreateUserFile(&UserFile{ID:"a2", UserID:1, Object:"file", Filename:"two.txt", Purpose:"user_data", Bytes:2, StorageKey:"1/a2"}))
    require.NoError(t, CreateUserFile(&UserFile{ID:"b1", UserID:2, Object:"file", Filename:"private.txt", Purpose:"user_data", Bytes:3, StorageKey:"2/b1"}))

    own, err := GetUserFile(1, "a1")
    require.NoError(t, err)
    require.Equal(t, 1, own.UserID)
    _, err = GetUserFile(1, "b1")
    require.ErrorIs(t, err, gorm.ErrRecordNotFound)

    files, err := ListUserFiles(1, 1, "")
    require.NoError(t, err)
    require.Len(t, files, 1)
    _, err = ListUserFiles(1, 20, "b1")
    require.ErrorIs(t, err, gorm.ErrRecordNotFound)

    deleted, err := DeleteUserFile(1, "a1")
    require.NoError(t, err)
    require.Equal(t, "a1", deleted.ID)
    _, err = GetUserFile(1, "a1")
    require.ErrorIs(t, err, gorm.ErrRecordNotFound)
    _, err = GetUserFile(2, "a2")
    require.ErrorIs(t, err, gorm.ErrRecordNotFound)
}
'''

test = r'''package controller

import (
    "bytes"
    "encoding/json"
    "mime/multipart"
    "net/http"
    "net/http/httptest"
    "os"
    "testing"

    "github.com/QuantumNous/new-api/model"
    "github.com/gin-gonic/gin"
    "github.com/stretchr/testify/require"
    "gorm.io/driver/sqlite"
    "gorm.io/gorm"
)

func userFileTestContext(t *testing.T, method, target string, body *bytes.Buffer, contentType string, userID int) (*gin.Context, *httptest.ResponseRecorder) {
    t.Helper()
    gin.SetMode(gin.TestMode)
    recorder := httptest.NewRecorder()
    context, _ := gin.CreateTestContext(recorder)
    request := httptest.NewRequest(method, target, body)
    if contentType != "" { request.Header.Set("Content-Type", contentType) }
    context.Request = request
    context.Set("id", userID)
    return context, recorder
}

func TestUserFileUploadListContentDeleteAndOwnerIsolation(t *testing.T) {
    previous := model.DB
    database, err := gorm.Open(sqlite.Open(":memory:"), &gorm.Config{})
    require.NoError(t, err)
    model.DB = database
    sqlDB, err := database.DB()
    require.NoError(t, err)
    t.Cleanup(func() { model.DB = previous; _ = sqlDB.Close() })
    require.NoError(t, database.AutoMigrate(&model.UserFile{}))
    root := t.TempDir()
    t.Setenv("TOKENWAY_FILE_STORAGE_DIR", root)

    var body bytes.Buffer
    multipartWriter := multipart.NewWriter(&body)
    require.NoError(t, multipartWriter.WriteField("purpose", "user_data"))
    part, err := multipartWriter.CreateFormFile("file", "hello.txt")
    require.NoError(t, err)
    _, err = part.Write([]byte("private file payload"))
    require.NoError(t, err)
    require.NoError(t, multipartWriter.Close())

    context, recorder := userFileTestContext(t, http.MethodPost, "/v1/files", &body, multipartWriter.FormDataContentType(), 71)
    UploadUserFile(context)
    require.Equal(t, http.StatusOK, recorder.Code)
    var uploaded map[string]any
    require.NoError(t, json.Unmarshal(recorder.Body.Bytes(), &uploaded))
    id, ok := uploaded["id"].(string)
    require.True(t, ok)
    require.NotEmpty(t, id)

    context, recorder = userFileTestContext(t, http.MethodGet, "/v1/files/"+id+"/content", nil, "", 72)
    context.Params = gin.Params{{Key:"id", Value:id}}
    GetUserFileContent(context)
    require.Equal(t, http.StatusNotFound, recorder.Code)

    context, recorder = userFileTestContext(t, http.MethodGet, "/v1/files", nil, "", 71)
    ListUserFiles(context)
    require.Equal(t, http.StatusOK, recorder.Code)
    require.Contains(t, recorder.Body.String(), id)

    context, recorder = userFileTestContext(t, http.MethodGet, "/v1/files/"+id+"/content", nil, "", 71)
    context.Params = gin.Params{{Key:"id", Value:id}}
    GetUserFileContent(context)
    require.Equal(t, http.StatusOK, recorder.Code)
    require.Equal(t, "private file payload", recorder.Body.String())

    context, recorder = userFileTestContext(t, http.MethodDelete, "/v1/files/"+id, nil, "", 72)
    context.Params = gin.Params{{Key:"id", Value:id}}
    DeleteUserFile(context)
    require.Equal(t, http.StatusNotFound, recorder.Code)

    context, recorder = userFileTestContext(t, http.MethodDelete, "/v1/files/"+id, nil, "", 71)
    context.Params = gin.Params{{Key:"id", Value:id}}
    DeleteUserFile(context)
    require.Equal(t, http.StatusOK, recorder.Code)
    _, err = os.Stat(userFilePath(root, 71, id))
    require.ErrorIs(t, err, os.ErrNotExist)
}
'''

write_new("model/user_file.go", model)
write_new("controller/user_file.go", controller)
write_new("model/user_file_test.go", model_test)
write_new("controller/user_file_test.go", test)

replace_once(
    "model/main.go",
    "\t\t&UserAccessToken{},\n\t)",
    "\t\t&UserAccessToken{},\n\t\t&UserFile{},\n\t)",
    "AutoMigrate UserFile",
)
replace_once(
    "router/relay-router.go",
    '\t\thttpRouter.GET("/files", controller.RelayNotImplemented)\n\t\thttpRouter.POST("/files", controller.RelayNotImplemented)\n\t\thttpRouter.DELETE("/files/:id", controller.RelayNotImplemented)\n\t\thttpRouter.GET("/files/:id/content", controller.RelayNotImplemented)',
    '\t\thttpRouter.GET("/files", controller.ListUserFiles)\n\t\thttpRouter.POST("/files", controller.UploadUserFile)\n\t\thttpRouter.DELETE("/files/:id", controller.DeleteUserFile)\n\t\thttpRouter.GET("/files/:id/content", controller.GetUserFileContent)',
    "OpenAI files endpoints",
)
print("PASS: owner-scoped file library backend, migration, routes, and focused tests applied")
