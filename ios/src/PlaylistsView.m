#import "Views.h"
#import "Library.h"
#import "Server.h"

@implementation PlaylistsView {
    Library *_library;
    Server *_server;
    NSArray *_lists;
    NSString *_status;
    BOOL _busy;
}

- (instancetype)initWithLibrary:(Library *)library {
    if ((self = [super initWithStyle:UITableViewStyleGrouped])) {
        _library = library;
        self.title = @"Playlists";
        NSString *saved = [[NSUserDefaults standardUserDefaults] stringForKey:@"ServerOrigin"];
        if (saved) _server = [[Server alloc] initWithOrigin:saved];
    }
    return self;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.navigationItem.leftBarButtonItem = Button(@"Settings", self, @selector(settings));
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemRefresh
        target:self action:@selector(refresh)];
    if (_server) [self refresh];
}

- (void)state:(NSString *)status busy:(BOOL)busy {
    _status = status; _busy = busy;
    self.navigationItem.leftBarButtonItem.enabled = !busy;
    self.navigationItem.rightBarButtonItem.enabled = !busy;
    [self.tableView reloadData];
}

- (void)settings {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"Server settings"
        message:@"Enter the HTTPS address of your music server and its library token." preferredStyle:UIAlertControllerStyleAlert];
    [a addTextFieldWithConfigurationHandler:^(UITextField *f) {
        f.placeholder = @"https://your-service.up.railway.app"; f.text = self->_server.origin;
        f.keyboardType = UIKeyboardTypeURL; f.autocapitalizationType = UITextAutocapitalizationTypeNone;
        f.autocorrectionType = UITextAutocorrectionTypeNo;
    }];
    [a addTextFieldWithConfigurationHandler:^(UITextField *f) {
        f.placeholder = @"Library token"; f.secureTextEntry = YES;
        f.autocapitalizationType = UITextAutocapitalizationTypeNone; f.autocorrectionType = UITextAutocorrectionTypeNo;
    }];
    [a addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:nil]];
    NSArray *fields = a.textFields;
    [a addAction:[UIAlertAction actionWithTitle:@"Connect" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        NSString *origin = [Server normalizedOrigin:[fields[0] text]];
        if (!origin) { [self state:@"Use an HTTPS server address with no path, login, query or fragment." busy:NO]; return; }
        NSString *token = [fields[1] text]; [fields[1] setText:nil];
        if (![Server storeToken:token forOrigin:origin]) {
            [self state:@"Token not saved. Enter a nonempty library token while the iPad is unlocked." busy:NO]; return;
        }
        self->_server = [[Server alloc] initWithOrigin:origin];
        self->_lists = nil;
        [[NSUserDefaults standardUserDefaults] setObject:origin forKey:@"ServerOrigin"];
        [self refresh];
    }]];
    [self presentViewController:a animated:YES completion:nil];
}

- (void)loadWithMessage:(NSString *)message {
    [self state:@"Loading playlists…" busy:YES];
    [_server catalog:^(NSArray *lists, NSString *error) {
        if (!error) self->_lists = lists;
        NSString *status = error ? [NSString stringWithFormat:@"%@%@", message ? [message stringByAppendingString:@"  "] : @"", error] :
            (message ?: (lists.count ? @"Choose a playlist to see matches and save tracks." : @"No playlists yet. Import a Spotify playlist link."));
        [self state:status busy:NO];
    }];
}

- (void)refresh {
    if (_busy) return;
    if (!_server) { [self settings]; return; }
    [self loadWithMessage:nil];
}

- (void)importLink {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"Import Spotify playlist"
        message:@"Import the track list. Only audio already uploaded to your server can be saved." preferredStyle:UIAlertControllerStyleAlert];
    [a addTextFieldWithConfigurationHandler:^(UITextField *f) {
        f.placeholder = @"Spotify playlist link"; f.keyboardType = UIKeyboardTypeURL;
        f.autocorrectionType = UITextAutocorrectionTypeNo; f.autocapitalizationType = UITextAutocapitalizationTypeNone;
    }];
    UITextField *field = a.textFields.firstObject;
    [a addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:nil]];
    [a addAction:[UIAlertAction actionWithTitle:@"Import" style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
        NSString *link = [field.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if (!link.length) { [self state:@"Paste a Spotify playlist link to import." busy:NO]; return; }
        [self state:@"Importing playlist metadata…" busy:YES];
        [self->_server importLink:link completion:^(NSDictionary *r, NSString *error) {
            if (error) { [self state:error busy:NO]; return; }
            [self loadWithMessage:[NSString stringWithFormat:@"Imported %@: %@ matched, %@ missing.",
                Text(r[@"name"], @"playlist"), r[@"matched"], r[@"missing_count"]]];
        }];
    }]];
    [self presentViewController:a animated:YES completion:nil];
}

- (NSInteger)numberOfSectionsInTableView:(UITableView *)table { return 2; }
- (NSInteger)tableView:(UITableView *)table numberOfRowsInSection:(NSInteger)section { return section ? _lists.count : 1; }
- (NSString *)tableView:(UITableView *)table titleForHeaderInSection:(NSInteger)section {
    return section ? @"Playlists" : (_server.origin ?: @"Not connected");
}
- (NSString *)tableView:(UITableView *)table titleForFooterInSection:(NSInteger)section {
    return section ? nil : (_status ?: @"Connect your music server to browse uploaded music.");
}
- (UITableViewCell *)tableView:(UITableView *)table cellForRowAtIndexPath:(NSIndexPath *)path {
    if (path.section == 0) {
        UITableViewCell *cell = Row(table, @"action");
        cell.textLabel.text = _server ? @"Import Spotify playlist…" : @"Connect a server…";
        cell.textLabel.textColor = _busy ? UIColor.grayColor : self.view.tintColor;
        return cell;
    }
    NSDictionary *p = _lists[path.row];
    UITableViewCell *cell = Row(table, @"playlist");
    cell.textLabel.text = Text(p[@"name"], @"Unnamed playlist");
    NSUInteger matches = 0;
    for (NSDictionary *t in p[@"tracks"]) if (MatchedID(t)) matches++;
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%lu of %lu tracks matched",
        (unsigned long)matches, (unsigned long)[p[@"tracks"] count]];
    cell.accessoryType = UITableViewCellAccessoryDisclosureIndicator;
    return cell;
}
- (void)tableView:(UITableView *)table didSelectRowAtIndexPath:(NSIndexPath *)path {
    [table deselectRowAtIndexPath:path animated:YES];
    if (_busy) return;
    if (path.section == 0) {
        if (_server) [self importLink]; else [self settings];
    } else {
        TracksView *tracks = [[TracksView alloc] initWithPlaylist:_lists[path.row] server:_server library:_library];
        [self.navigationController pushViewController:tracks animated:YES];
    }
}
@end
