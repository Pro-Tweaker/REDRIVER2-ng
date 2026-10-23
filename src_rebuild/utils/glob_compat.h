#ifndef GLOB_COMPAT_H
#define GLOB_COMPAT_H

// bionic only provides glob() since Android 9 (API 28). This is a minimal replacement
// (single directory, wildcard in the last path component only), as used by fs.cpp.

#include <dirent.h>
#include <fnmatch.h>
#include <stdlib.h>
#include <string.h>

typedef struct
{
	size_t	gl_pathc;
	char**	gl_pathv;
} glob_t;

static inline void globfree(glob_t* g)
{
	for (size_t i = 0; i < g->gl_pathc; i++)
		free(g->gl_pathv[i]);

	free(g->gl_pathv);
	g->gl_pathc = 0;
	g->gl_pathv = NULL;
}

static inline int glob(const char* pattern, int flags, void* errfunc, glob_t* g)
{
	(void)flags; (void)errfunc;

	g->gl_pathc = 0;
	g->gl_pathv = NULL;

	const char* slash = strrchr(pattern, '/');
	size_t dirLen = slash ? (size_t)(slash - pattern) + 1 : 0;

	char* dirName = (char*)calloc(dirLen + 2, 1);
	if (dirLen)
		memcpy(dirName, pattern, dirLen);
	else
		strcpy(dirName, ".");

	const char* namePattern = pattern + dirLen;

	DIR* dir = opendir(dirName);
	free(dirName);

	if (!dir)
		return 1;

	size_t capacity = 0;
	struct dirent* ent;
	while ((ent = readdir(dir)) != NULL)
	{
		if (fnmatch(namePattern, ent->d_name, 0) != 0)
			continue;

		if (g->gl_pathc == capacity)
		{
			capacity = capacity ? capacity * 2 : 16;
			g->gl_pathv = (char**)realloc(g->gl_pathv, capacity * sizeof(char*));
		}

		size_t nameLen = strlen(ent->d_name);
		char* path = (char*)malloc(dirLen + nameLen + 1);
		memcpy(path, pattern, dirLen);
		memcpy(path + dirLen, ent->d_name, nameLen + 1);
		g->gl_pathv[g->gl_pathc++] = path;
	}
	closedir(dir);

	return g->gl_pathc ? 0 : 1;
}

#endif // GLOB_COMPAT_H
